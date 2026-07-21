"""Per-user recommendation state, stored in PostgreSQL.

All methods are async and take the osu! username (case-insensitively) as the key.
A one-time importer migrates the legacy data.json file on first start.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .db import Database

log = logging.getLogger("spotiosu.store")


class Store:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def _pool(self):
        if self._db.pool is None:
            raise RuntimeError("Database is not connected")
        return self._db.pool

    @staticmethod
    def _key(username: str) -> str:
        return username.lower()

    async def _ensure_user(self, conn, username: str) -> None:
        await conn.execute(
            "INSERT INTO users (username) VALUES ($1) ON CONFLICT DO NOTHING",
            self._key(username),
        )

    # ---- onboarding ---------------------------------------------------------
    async def is_onboarded(self, username: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT onboarded FROM users WHERE username = $1", self._key(username)
            )
        return bool(row and row["onboarded"])

    async def set_onboarded(self, username: str, value: bool = True) -> None:
        async with self._pool.acquire() as conn:
            await self._ensure_user(conn, username)
            await conn.execute(
                "UPDATE users SET onboarded = $2, updated_at = now() WHERE username = $1",
                self._key(username), value,
            )

    async def get_genres(self, username: str) -> list[int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT genres FROM users WHERE username = $1", self._key(username)
            )
        return list(row["genres"]) if row and row["genres"] else []

    async def set_genres(self, username: str, genres: list[int]) -> None:
        async with self._pool.acquire() as conn:
            await self._ensure_user(conn, username)
            await conn.execute(
                "UPDATE users SET genres = $2, updated_at = now() WHERE username = $1",
                self._key(username), [int(g) for g in genres],
            )

    async def reset_onboarding(self, username: str) -> None:
        key = self._key(username)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user(conn, username)
                await conn.execute(
                    "UPDATE users SET onboarded = FALSE, genres = '{}', last_rec = NULL,"
                    " updated_at = now() WHERE username = $1", key,
                )
                await conn.execute("DELETE FROM ratings WHERE username = $1", key)
                await conn.execute("DELETE FROM seen    WHERE username = $1", key)
                await conn.execute("DELETE FROM likes   WHERE username = $1", key)

    # ---- ratings ------------------------------------------------------------
    async def add_rating(self, username: str, rating: dict[str, Any],
                         limit: int = 500) -> None:
        key = self._key(username)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user(conn, username)
                await conn.execute(
                    """
                    INSERT INTO ratings
                        (username, set_id, beatmap_id, genre_id, stars, bpm, creator,
                         liked, title, artist, cover_url)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (username, set_id) DO UPDATE SET
                        beatmap_id = EXCLUDED.beatmap_id,
                        genre_id   = EXCLUDED.genre_id,
                        stars      = EXCLUDED.stars,
                        bpm        = EXCLUDED.bpm,
                        creator    = EXCLUDED.creator,
                        liked      = EXCLUDED.liked,
                        title      = EXCLUDED.title,
                        artist     = EXCLUDED.artist,
                        cover_url  = EXCLUDED.cover_url,
                        rated_at   = now()
                    """,
                    key, int(rating.get("set_id") or 0),
                    int(rating.get("beatmap_id") or 0) or None,
                    int(rating.get("genre_id") or 0) or None,
                    float(rating.get("stars") or 0),
                    float(rating.get("bpm") or 0),
                    (rating.get("creator") or "").strip() or None,
                    bool(rating.get("liked")),
                    (rating.get("title") or "").strip() or None,
                    (rating.get("artist") or "").strip() or None,
                    (rating.get("cover_url") or "").strip() or None,
                )
                await conn.execute(
                    """
                    DELETE FROM ratings WHERE username = $1 AND set_id NOT IN (
                        SELECT set_id FROM ratings WHERE username = $1
                        ORDER BY rated_at DESC LIMIT $2
                    )
                    """, key, limit,
                )

    async def get_ratings(self, username: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT set_id, beatmap_id, genre_id, stars, bpm, creator, liked,"
                " title, artist, cover_url, rated_at"
                " FROM ratings WHERE username = $1 ORDER BY rated_at",
                self._key(username),
            )
        return [dict(r) for r in rows]

    async def count_ratings(self, username: str) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM ratings WHERE username = $1", self._key(username)
            ) or 0

    # ---- seen / anti-repeat -------------------------------------------------
    async def get_seen(self, username: str) -> set[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT set_id FROM seen WHERE username = $1", self._key(username)
            )
        return {int(r["set_id"]) for r in rows}

    async def mark_seen(self, username: str, set_id: int, limit: int = 400) -> None:
        key = self._key(username)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user(conn, username)
                await conn.execute(
                    "INSERT INTO seen (username, set_id) VALUES ($1, $2)"
                    " ON CONFLICT (username, set_id) DO UPDATE SET seen_at = now()",
                    key, int(set_id),
                )
                await conn.execute(
                    """
                    DELETE FROM seen WHERE username = $1 AND set_id NOT IN (
                        SELECT set_id FROM seen WHERE username = $1
                        ORDER BY seen_at DESC LIMIT $2
                    )
                    """, key, limit,
                )

    async def mark_seen_many(self, username: str, set_ids: list[int],
                             limit: int = 400) -> None:
        """Batch version of mark_seen - one round-trip instead of one per map."""
        if not set_ids:
            return
        key = self._key(username)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user(conn, username)
                await conn.executemany(
                    "INSERT INTO seen (username, set_id) VALUES ($1, $2)"
                    " ON CONFLICT (username, set_id) DO UPDATE SET seen_at = now()",
                    [(key, int(s)) for s in set_ids],
                )
                await conn.execute(
                    """
                    DELETE FROM seen WHERE username = $1 AND set_id NOT IN (
                        SELECT set_id FROM seen WHERE username = $1
                        ORDER BY seen_at DESC LIMIT $2
                    )
                    """, key, limit,
                )

    async def reset_seen(self, username: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM seen WHERE username = $1", self._key(username))

    # ---- last recommendation (used by the CLI bot's !with) ------------------
    async def set_last(self, username: str, rec: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await self._ensure_user(conn, username)
            await conn.execute(
                "UPDATE users SET last_rec = $2::jsonb, updated_at = now()"
                " WHERE username = $1",
                self._key(username), json.dumps(rec),
            )

    async def get_last(self, username: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_rec FROM users WHERE username = $1", self._key(username)
            )
        if not row or row["last_rec"] is None:
            return None
        value = row["last_rec"]
        return json.loads(value) if isinstance(value, str) else dict(value)

    # ---- learned weights ----------------------------------------------------
    async def adjust_like(self, username: str, key_name: str, delta: float) -> None:
        async with self._pool.acquire() as conn:
            await self._ensure_user(conn, username)
            await conn.execute(
                """
                INSERT INTO likes (username, key, weight) VALUES ($1, $2, $3)
                ON CONFLICT (username, key)
                DO UPDATE SET weight = likes.weight + EXCLUDED.weight
                """,
                self._key(username), key_name, float(delta),
            )

    async def get_likes(self, username: str) -> dict[str, float]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, weight FROM likes WHERE username = $1", self._key(username)
            )
        return {r["key"]: float(r["weight"]) for r in rows}


# ---- legacy data.json import ------------------------------------------------
async def migrate_json_file(store: Store, path: str | Path = "data.json") -> int:
    """Import a legacy JSON store into Postgres. Returns the number of users moved.

    Runs only when the database is still empty, so it is safe to call on boot.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    async with store._pool.acquire() as conn:  # noqa: SLF001 - internal helper
        if await conn.fetchval("SELECT count(*) FROM users"):
            return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Legacy %s is unreadable; skipping migration", path)
        return 0

    moved = 0
    for username, u in (data.get("users") or {}).items():
        if u.get("genres"):
            await store.set_genres(username, u["genres"])
        if u.get("onboarded"):
            await store.set_onboarded(username, True)
        for rating in u.get("ratings") or []:
            await store.add_rating(username, rating)
        for set_id in u.get("seen_set_ids") or []:
            await store.mark_seen(username, int(set_id))
        for key_name, weight in (u.get("likes") or {}).items():
            await store.adjust_like(username, key_name, float(weight))
        if u.get("last"):
            await store.set_last(username, u["last"])
        moved += 1
    if moved:
        backup = path.with_suffix(".json.migrated")
        try:
            path.replace(backup)
        except OSError:
            pass
        log.info("Migrated %d user(s) from %s into PostgreSQL (backup: %s)",
                 moved, path, backup)
    return moved
