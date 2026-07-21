"""Simple JSON-backed persistence for per-user recommendation state.

Kept intentionally small: one file, loaded on start, written after each change.
For a personal bot this is plenty; swapping in SQLite later is straightforward.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("spotiosu.store")


class Store:
    def __init__(self, path: str | Path = "data.json") -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = {"users": {}}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                self._data.setdefault("users", {})
            except (json.JSONDecodeError, OSError):
                log.warning("Could not read %s; starting fresh", self._path)

    def _save(self) -> None:
        """Persist atomically: a crash mid-write must not truncate existing data."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)  # atomic on POSIX and Windows
        except OSError:
            log.exception("Failed to persist store")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _user(self, username: str) -> dict[str, Any]:
        key = username.lower()
        users = self._data["users"]
        if key not in users:
            users[key] = {}
        u = users[key]
        u.setdefault("seen_set_ids", [])  # recently recommended beatmapset ids
        u.setdefault("likes", {})         # feature -> weight adjustments
        u.setdefault("last", None)        # last recommended beatmap (for !with)
        u.setdefault("onboarded", False)  # finished the genre/taste questionnaire
        u.setdefault("genres", [])        # genre ids picked during onboarding
        u.setdefault("ratings", [])       # per-track like/dislike history
        return u

    # ---- onboarding ---------------------------------------------------------
    def is_onboarded(self, username: str) -> bool:
        return bool(self._user(username)["onboarded"])

    def set_onboarded(self, username: str, value: bool = True) -> None:
        self._user(username)["onboarded"] = value
        self._save()

    def get_genres(self, username: str) -> list[int]:
        return list(self._user(username)["genres"])

    def set_genres(self, username: str, genres: list[int]) -> None:
        self._user(username)["genres"] = [int(g) for g in genres]
        self._save()

    def reset_onboarding(self, username: str) -> None:
        u = self._user(username)
        u["onboarded"] = False
        u["genres"] = []
        u["ratings"] = []
        u["likes"] = {}
        u["seen_set_ids"] = []
        self._save()

    # ---- rating history (drives the taste profile) --------------------------
    def add_rating(self, username: str, rating: dict[str, Any], limit: int = 500) -> None:
        ratings = self._user(username)["ratings"]
        set_id = rating.get("set_id")
        # One verdict per beatmapset: a later rating replaces an earlier one.
        for i, existing in enumerate(ratings):
            if existing.get("set_id") == set_id:
                ratings[i] = rating
                break
        else:
            ratings.append(rating)
        if len(ratings) > limit:
            del ratings[: len(ratings) - limit]
        self._save()

    def get_ratings(self, username: str) -> list[dict[str, Any]]:
        return list(self._user(username)["ratings"])

    # ---- seen / anti-repeat -------------------------------------------------
    def get_seen(self, username: str) -> set[int]:
        return set(self._user(username)["seen_set_ids"])

    def mark_seen(self, username: str, set_id: int, limit: int = 400) -> None:
        u = self._user(username)
        seen = u["seen_set_ids"]
        if set_id in seen:
            seen.remove(set_id)
        seen.append(set_id)
        if len(seen) > limit:
            del seen[: len(seen) - limit]
        self._save()

    def reset_seen(self, username: str) -> None:
        self._user(username)["seen_set_ids"] = []
        self._save()

    # ---- last recommendation (for !with mods) -------------------------------
    def set_last(self, username: str, rec: dict[str, Any]) -> None:
        self._user(username)["last"] = rec
        self._save()

    def get_last(self, username: str) -> dict[str, Any] | None:
        return self._user(username)["last"]

    # ---- like / dislike preference nudges -----------------------------------
    def adjust_like(self, username: str, key: str, delta: float) -> None:
        likes = self._user(username)["likes"]
        likes[key] = round(likes.get(key, 0.0) + delta, 3)
        self._save()

    def get_likes(self, username: str) -> dict[str, float]:
        return dict(self._user(username)["likes"])
