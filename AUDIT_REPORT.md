# Production cleanup / audit report

## Completed in this pass

- Removed the duplicate `services/user service.py` module.
- Compiled all Python sources successfully with Python 3.13 in the audit environment.
- Added GitHub CI that installs the declared dependencies, compiles the project and runs the full pytest suite.
- Kept Alembic as the production schema source of truth; `AUTO_SCHEMA_UPGRADE` now defaults to `false`.
- Added configurable PostgreSQL pool sizing (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`) with conservative Railway defaults.
- Hardened Telegram Stars order completion with a PostgreSQL row lock so concurrent `successful_payment` updates cannot both consume the same pending order.
- Hardened scheduled card drops: failed Telegram sends now clean up the orphaned active drop and mark the schedule as failed instead of leaving stale state behind.
- A `TelegramForbiddenError` disables the affected group; a generic `TelegramBadRequest` no longer disables a healthy group automatically.
- Added Railway/production configuration notes to README and `.env.example`.
- Removed generated Python caches before release packaging.

## Verification

- `python -m compileall -q .` passes.
- Internal Python module references were statically checked during the audit.
- Duplicate top-level/class method definitions were checked; none were found.
- Full pytest execution was attempted. The audit container does not have `aiosqlite` installed and has no network access to install it, so pytest collection stops at `ModuleNotFoundError: aiosqlite`. `aiosqlite` is already declared in `requirements.txt`, and the GitHub CI workflow installs it before running tests.

## Railway/PostgreSQL

The repository is already structured for Railway PostgreSQL. `railway.toml` runs `alembic upgrade head` before `python -m app.main`. The application normalizes Railway's standard PostgreSQL URL to `postgresql+asyncpg://` and uses a bounded async connection pool.

## Remaining high-risk areas for the next refactor

- Very large admin/handler modules should be split into feature modules without changing behavior.
- Broad `except Exception` blocks should be reduced where an error can safely be handled more specifically.
- High-contention economy operations should continue to use atomic SQL updates / row locks where necessary.
- Full integration tests need to run in an environment with declared dependencies and a real PostgreSQL/Redis service for production-like coverage.
