"""osu! OAuth2 Authorization Code helpers (used for 'Sign in with osu!').

This is separate from the client_credentials 'guest' token used for data:
here we authenticate the *visitor* so the site knows whose profile to build a
feed for. We only need their identity, so the requested scope is 'identify'.
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://osu.ppy.sh/oauth/authorize"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
ME_URL = "https://osu.ppy.sh/api/v2/me"


def authorize_url(client_id: int, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    client_id: int, client_secret: str, code: str, redirect_uri: str
) -> str:
    """Exchange an authorization code for a user access token."""
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.post(
            TOKEN_URL,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def fetch_me(user_token: str) -> dict:
    """Fetch the authenticated user's profile."""
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(
            ME_URL,
            headers={"Authorization": f"Bearer {user_token}", "Accept": "application/json"},
        )
    resp.raise_for_status()
    data = resp.json()
    stats = data.get("statistics") or {}
    return {
        "id": int(data["id"]),
        "username": data.get("username", ""),
        "avatar_url": data.get("avatar_url", ""),
        "pp": round(float(stats.get("pp") or 0), 0),
        "global_rank": stats.get("global_rank"),
        "mode": data.get("playmode", "osu"),
        "cover_url": (data.get("cover") or {}).get("url", ""),
    }
