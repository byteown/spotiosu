"""End-to-end check of the PostgreSQL storage layer.

Runs in CI against a real Postgres service container. This is the only place the
SQL actually executes, so it exercises every Store method and asserts the values
round-trip correctly.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from bot.db import Database          # noqa: E402
from bot.store import Store          # noqa: E402

USER = "TestUser"          # deliberately mixed case: keys must be case-insensitive
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{' - ' + detail if detail else ''}")
    if not condition:
        FAILURES.append(label)


async def main() -> int:
    dsn = os.environ["SPOTIOSU_DSN"]
    db = Database(dsn)
    await db.connect()
    store = Store(db)

    # Start from a clean slate so re-runs are deterministic.
    async with db.pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE username = $1", USER.lower())

    print("onboarding")
    check("new user is not onboarded", await store.is_onboarded(USER) is False)
    await store.set_genres(USER, [3, 10])
    check("genres round-trip", await store.get_genres(USER) == [3, 10],
          str(await store.get_genres(USER)))
    await store.set_onboarded(USER, True)
    check("onboarded flag persists", await store.is_onboarded(USER) is True)
    check("username is case-insensitive", await store.get_genres(USER.lower()) == [3, 10])

    print("ratings")
    await store.add_rating(USER, {"set_id": 111, "beatmap_id": 222, "genre_id": 10,
                                  "stars": 4.5, "bpm": 175.0, "creator": "Mapper",
                                  "liked": True, "title": "Sőng Tïtle",
                                  "artist": "Artist", "cover_url": "https://x/c.jpg"})
    await store.add_rating(USER, {"set_id": 112, "beatmap_id": 223, "genre_id": 3,
                                  "stars": 3.1, "bpm": 140.0, "creator": "Other",
                                  "liked": False})
    ratings = await store.get_ratings(USER)
    check("two ratings stored", len(ratings) == 2, str(len(ratings)))
    check("count_ratings agrees", await store.count_ratings(USER) == 2)
    first = next(r for r in ratings if r["set_id"] == 111)
    check("liked flag", first["liked"] is True)
    check("stars survived", abs(float(first["stars"]) - 4.5) < 0.01, str(first["stars"]))
    check("creator survived", first["creator"] == "Mapper")
    # Columns added later via ALTER TABLE ... IF NOT EXISTS - this proves the
    # migration ran and the profile page will have artwork to show.
    check("artwork columns exist", "cover_url" in first, str(sorted(first)))
    check("title round-trips (unicode)", first.get("title") == "Sőng Tïtle",
          str(first.get("title")))
    check("cover_url round-trips", first.get("cover_url") == "https://x/c.jpg")

    # Re-rating the same beatmapset must replace, not duplicate.
    await store.add_rating(USER, {"set_id": 111, "genre_id": 10, "stars": 4.5,
                                  "bpm": 175.0, "creator": "Mapper", "liked": False})
    ratings = await store.get_ratings(USER)
    flipped = next(r for r in ratings if r["set_id"] == 111)
    check("re-rating replaces", len(ratings) == 2 and flipped["liked"] is False,
          f"{len(ratings)} rows, liked={flipped['liked']}")

    print("seen / anti-repeat")
    await store.mark_seen(USER, 900)
    await store.mark_seen_many(USER, [901, 902, 903])
    seen = await store.get_seen(USER)
    check("mark_seen + mark_seen_many", seen == {900, 901, 902, 903}, str(sorted(seen)))
    await store.mark_seen_many(USER, [904, 905], limit=3)
    seen = await store.get_seen(USER)
    check("limit trims oldest", len(seen) == 3, str(sorted(seen)))
    await store.reset_seen(USER)
    check("reset_seen clears", await store.get_seen(USER) == set())

    print("likes / weights")
    await store.adjust_like(USER, "mapper:Mapper", 0.4)
    await store.adjust_like(USER, "mapper:Mapper", 0.4)
    likes = await store.get_likes(USER)
    check("weights accumulate", abs(likes.get("mapper:Mapper", 0) - 0.8) < 0.01,
          str(likes))

    print("last recommendation (jsonb)")
    await store.set_last(USER, {"beatmap_id": 5, "title": "Ünïcøde ✓", "stars": 4.2})
    last = await store.get_last(USER)
    check("jsonb round-trip", last is not None and last["title"] == "Ünïcøde ✓", str(last))

    print("reset")
    await store.reset_onboarding(USER)
    check("onboarding reset", await store.is_onboarded(USER) is False)
    check("ratings cleared", await store.count_ratings(USER) == 0)
    check("genres cleared", await store.get_genres(USER) == [])
    check("likes cleared", await store.get_likes(USER) == {})

    async with db.pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE username = $1", USER.lower())
    await db.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("\nall storage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
