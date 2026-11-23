import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Optional

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class LockManager:
    """
    Provides atomic distributed locks, cooldown checks, and callback deduplication.
    Uses Redis in production and falls back cleanly to asyncio locks in local mode.
    """

    _RELEASE_LUA = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, redis_client: Optional[Redis] = None) -> None:
        self._redis = redis_client
        self._local_locks: Dict[str, tuple[str, float]] = {}
        self._local_expiries: Dict[str, float] = {}
        self._guard = asyncio.Lock()

    @property
    def redis(self) -> Optional[Redis]:
        return self._redis

    @asynccontextmanager
    async def lock(self, key: str, ttl_ms: int = 5000) -> AsyncGenerator[bool, None]:
        full_key = f"animecards:lock:{key}"
        if self._redis is not None:
            token = str(uuid.uuid4())
            acquired = await self._redis.set(full_key, token, nx=True, px=ttl_ms)
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    try:
                        await self._redis.eval(self._RELEASE_LUA, 1, full_key, token)
                    except Exception as exc:
                        logger.warning("Failed to release Redis lock %s: %s", full_key, exc)
            return

        # Local single-process fallback. Keep the same fail-fast + TTL semantics as Redis.
        token = str(uuid.uuid4())
        now = time.monotonic()
        ttl_seconds = max(0.1, ttl_ms / 1000.0)
        async with self._guard:
            current = self._local_locks.get(full_key)
            if current is not None and current[1] > now:
                acquired = False
            else:
                self._local_locks[full_key] = (token, now + ttl_seconds)
                acquired = True
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            async with self._guard:
                current = self._local_locks.get(full_key)
                if current is not None and current[0] == token:
                    self._local_locks.pop(full_key, None)

    async def check_and_set_cooldown(self, key: str, seconds: float) -> bool:
        """Returns True if action is allowed (and records cooldown), False if still cooling down."""
        full_key = f"animecards:cd:{key}"
        ttl_ms = max(100, int(seconds * 1000))
        if self._redis is not None:
            allowed = await self._redis.set(full_key, "1", nx=True, px=ttl_ms)
            return bool(allowed)

        now = time.monotonic()
        async with self._guard:
            expires_at = self._local_expiries.get(full_key, 0.0)
            if expires_at > now:
                return False
            self._local_expiries[full_key] = now + max(0.0, seconds)
            # Bound local fallback memory without scanning on every request.
            if len(self._local_expiries) > 10_000:
                expired = [k for k, v in self._local_expiries.items() if v <= now]
                for k in expired:
                    self._local_expiries.pop(k, None)
            if len(self._local_locks) > 10_000:
                stale_locks = [k for k, (_, expiry) in self._local_locks.items() if expiry <= now]
                for k in stale_locks:
                    self._local_locks.pop(k, None)
            return True

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


_lock_manager: Optional[LockManager] = None


async def init_lock_manager(redis_url: str) -> LockManager:
    global _lock_manager
    if redis_url:
        try:
            client = Redis.from_url(redis_url, decode_responses=True)
            await client.ping()
            logger.info("Redis connected successfully for locks, cooldowns, and FSM.")
            _lock_manager = LockManager(client)
            return _lock_manager
        except Exception as exc:
            logger.warning("Redis unavailable (%s). Falling back to in-memory lock manager.", exc)

    _lock_manager = LockManager(None)
    return _lock_manager


def get_lock_manager() -> LockManager:
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = LockManager(None)
    return _lock_manager
