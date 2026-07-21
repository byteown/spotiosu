"""Thin async wrapper around the osu! API v2 (plus the public .osu file endpoint).

Auth uses the client_credentials grant, which yields a "guest" token that can
read public data: user profiles, users' best scores, and beatmap search — which
is everything the recommender needs.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_BASE = "https://osu.ppy.sh/api/v2"
OSU_FILE_URL = "https://osu.ppy.sh/osu/{beatmap_id}"

# osu! ruleset id <-> name
MODE_TO_RULESET = {"osu": 0, "taiko": 1, "fruits": 2, "mania": 3}
RULESET_TO_MODE = {v: k for k, v in MODE_TO_RULESET.items()}


class OsuApiError(Exception):
    pass


class OsuApi:
    def __init__(self, client_id: int, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        await self._http.aclose()

    # ---- auth ---------------------------------------------------------------
    async def _ensure_token(self) -> str:
        # Refresh a minute before actual expiry to avoid edge races.
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            resp = await self._http.post(
                TOKEN_URL,
                json={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                    "scope": "public",
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                raise OsuApiError(
                    f"Token request failed ({resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = time.time() + int(data.get("expires_in", 3600))
            return self._token

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        token = await self._ensure_token()
        resp = await self._http.get(
            f"{API_BASE}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code == 401:
            # Token might have been revoked early; force one refresh + retry.
            self._token = None
            token = await self._ensure_token()
            resp = await self._http.get(
                f"{API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise OsuApiError(f"GET {path} failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    # ---- endpoints ----------------------------------------------------------
    async def get_user(self, username_or_id: str, mode: str = "osu") -> dict | None:
        """Look up a user by username or id. Returns None if not found."""
        data = await self._get(f"/users/{username_or_id}/{mode}", params={"key": "username"})
        if data is None:
            # Fallback: id lookup (key=username fails for pure-numeric ids too).
            data = await self._get(f"/users/{username_or_id}/{mode}")
        return data

    async def get_user_best(
        self, user_id: int, mode: str = "osu", limit: int = 100
    ) -> list[dict]:
        data = await self._get(
            f"/users/{user_id}/scores/best",
            params={"mode": mode, "limit": limit, "include_fails": 0},
        )
        return data or []

    async def get_beatmap(self, beatmap_id: int) -> dict | None:
        return await self._get(f"/beatmaps/{beatmap_id}")

    async def search_beatmapsets(
        self, query: str = "", extra: dict[str, Any] | None = None
    ) -> dict:
        """Search ranked beatmapsets. `query` accepts the same advanced filter
        syntax as the website search bar (e.g. 'stars>4 stars<6 bpm>150')."""
        params: dict[str, Any] = {"q": query, "s": "ranked", "sort": "plays_desc"}
        if extra:
            params.update(extra)
        data = await self._get("/beatmapsets/search", params=params)
        return data or {"beatmapsets": []}

    async def download_osu_file(self, beatmap_id: int) -> str | None:
        """Fetch the raw .osu file text for a beatmap (used for pp calculation)."""
        try:
            resp = await self._http.get(OSU_FILE_URL.format(beatmap_id=beatmap_id))
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or not resp.text.strip():
            return None
        return resp.text
