"""osu! beatmap genre ids.

These match the genre filter on the osu! beatmap listing, and are passed to the
search endpoint as the `g` parameter. Note there is no id 8.
"""
from __future__ import annotations

GENRES: dict[int, str] = {
    3: "Anime",
    10: "Electronic",
    2: "Video Game",
    4: "Rock",
    5: "Pop",
    11: "Metal",
    9: "Hip Hop",
    12: "Classical",
    14: "Jazz",
    13: "Folk",
    7: "Novelty",
    6: "Other",
}

# Display order for the onboarding picker (most popular in osu! first).
GENRE_ORDER: list[int] = [3, 10, 2, 4, 5, 11, 9, 12, 14, 13, 7, 6]


def genre_name(genre_id: int | None) -> str:
    return GENRES.get(int(genre_id), "") if genre_id else ""


def genre_list() -> list[dict]:
    return [{"id": gid, "name": GENRES[gid]} for gid in GENRE_ORDER]
