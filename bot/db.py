"""PostgreSQL connection pool and schema management.

The schema is intentionally relational: one row per user, and separate tables for
ratings, seen maps and learned weights. That makes the taste data queryable
(e.g. "which genres does this user like?") instead of an opaque JSON blob.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg

log = logging.getLogger("spotiosu.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    onboarded  BOOLEAN     NOT NULL DEFAULT FALSE,
    genres     INTEGER[]   NOT NULL DEFAULT '{}',
    last_rec   JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ratings (
    username   TEXT   NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    set_id     BIGINT NOT NULL,
    beatmap_id BIGINT,
    genre_id   INTEGER,
    stars      REAL,
    bpm        REAL,
    creator    TEXT,
    liked      BOOLEAN     NOT NULL,
    rated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, set_id)
);
CREATE INDEX IF NOT EXISTS ratings_by_genre ON ratings (username, genre_id);

CREATE TABLE IF NOT EXISTS seen (
    username TEXT   NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    set_id   BIGINT NOT NULL,
    seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, set_id)
);
CREATE INDEX IF NOT EXISTS seen_recent ON seen (username, seen_at DESC);

CREATE TABLE IF NOT EXISTS likes (
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    weight   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (username, key)
);

-- Added later for the profile page: lets it show recently liked maps with their
-- artwork without calling the osu! API again. Idempotent, so it doubles as the
-- migration for databases created before the profile page existed.
ALTER TABLE ratings ADD COLUMN IF NOT EXISTS title     TEXT;
ALTER TABLE ratings ADD COLUMN IF NOT EXISTS artist    TEXT;
ALTER TABLE ratings ADD COLUMN IF NOT EXISTS cover_url TEXT;
"""


class Database:
    """Thin wrapper around an asyncpg pool."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self, *, retries: int = 10, delay: float = 1.5) -> None:
        """Open the pool and ensure the schema exists.

        Retries briefly so the app can start alongside a Postgres container that
        is still coming up (docker compose up).
        """
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=10, command_timeout=30
                )
                break
            except (OSError, asyncpg.PostgresError) as exc:
                last = exc
                log.warning("Postgres not ready (attempt %d/%d): %s", attempt, retries, exc)
                await asyncio.sleep(delay)
        if self.pool is None:
            raise RuntimeError(
                f"Could not connect to PostgreSQL at {_safe_dsn(self._dsn)}: {last}\n"
                "Is the database running?  Try:  docker compose up -d"
            )
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
        log.info("PostgreSQL connected (%s)", _safe_dsn(self._dsn))

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


def _safe_dsn(dsn: str) -> str:
    """Strip the password so DSNs are safe to log."""
    if "@" not in dsn:
        return dsn
    head, _, tail = dsn.rpartition("@")
    scheme, sep, creds = head.partition("://")
    user = creds.split(":", 1)[0]
    return f"{scheme}{sep}{user}:***@{tail}"
