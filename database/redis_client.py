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
        self._local_locks: Dict[str, asyncio.Lock] = {}
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

        # Local single-process fallback
        async with self._guard:
            lock_obj = self._local_locks.setdefault(full_key, asyncio.Lock())
        if lock_obj.locked():
            yield False
            return
        await lock_obj.acquire()
        try:
            yield True
        finally:
            lock_obj.release()

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
            self._local_expiries[full_key] = now + seconds
            if len(self._local_expiries) > 5000:
                expired = [k for k, v in self._local_expiries.items() if v <= now]
                for k in expired:
                    del self._local_expiries[k]
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
