"""FastAPI backend for the spotiosu web app.

Flow:
  1. Sign in with osu! (OAuth).
  2. First visit -> onboarding: pick genres, then rate sample tracks like/dislike.
  3. Main player: one map at a time, rating is mandatory and rebuilds the profile.

Reuses the recommendation engine from `bot/` (osu_api, recommender, pp, store).
"""
from __future__ import annotations

import logging
import math
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from bot.config import Config
from bot.db import Database
from bot.genres import genre_list, genre_name
from bot.osu_api import OsuApi
from bot.pp import PpCalculator
from bot.recommender import Recommender
from bot.store import Store, migrate_json_file

from . import oauth

log = logging.getLogger("spotiosu.web")

STATIC_DIR = Path(__file__).parent / "static"
SECRET_FILE = Path(".session_secret")

ONBOARDING_TARGET = 10  # ratings required to finish the questionnaire


def _session_secret(config: Config) -> str:
    """Resolve the session-signing secret.

    In production it comes from the environment so that redeploying the container
    does not log everybody out. Locally we fall back to a generated file.
    """
    if config.web.session_secret:
        return config.web.session_secret
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    try:
        SECRET_FILE.write_text(secret, encoding="utf-8")
    except OSError:
        log.warning("Could not persist %s - sessions will reset on restart", SECRET_FILE)
    return secret


# --- profile aggregations ----------------------------------------------------
def _avg(values: list[float]) -> float:
    clean = [v for v in values if v]
    return sum(clean) / len(clean) if clean else 0.0


def _totals(ratings: list[dict], liked: list[dict]) -> dict:
    total = len(ratings)
    return {
        "rated": total,
        "liked": len(liked),
        "disliked": total - len(liked),
        "like_rate": round(len(liked) / total * 100) if total else 0,
        "avg_bpm": round(_avg([r.get("bpm") for r in liked])),
        "avg_stars": round(_avg([r.get("stars") for r in liked]), 2),
    }


def _genre_breakdown(ratings: list[dict], weights: dict[int, float]) -> list[dict]:
    buckets: dict[int, dict[str, int]] = {}
    for r in ratings:
        gid = r.get("genre_id")
        if not gid:
            continue
        b = buckets.setdefault(int(gid), {"liked": 0, "disliked": 0})
        b["liked" if r.get("liked") else "disliked"] += 1
    out = [
        {
            "id": gid,
            "name": genre_name(gid) or "Other",
            "liked": b["liked"],
            "disliked": b["disliked"],
            "total": b["liked"] + b["disliked"],
            "affinity": round(weights.get(gid, 0.0), 2),
        }
        for gid, b in buckets.items()
    ]
    out.sort(key=lambda g: (-g["affinity"], -g["total"]))
    return out


def _difficulty_histogram(liked: list[dict]) -> list[dict]:
    """Liked maps bucketed into half-star steps."""
    buckets: dict[float, int] = {}
    for r in liked:
        stars = float(r.get("stars") or 0)
        if stars <= 0:
            continue
        key = math.floor(stars * 2) / 2
        buckets[key] = buckets.get(key, 0) + 1
    return [{"star": k, "count": v} for k, v in sorted(buckets.items())]


def _top_mappers(liked: list[dict], limit: int = 6) -> list[dict]:
    counts: dict[str, int] = {}
    for r in liked:
        creator = (r.get("creator") or "").strip()
        if creator:
            counts[creator] = counts.get(creator, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"name": name, "count": count} for name, count in ranked]


def _skill_vs_taste(profile, liked: list[dict]) -> dict:
    """Where their taste sits relative to what they can actually play.

    Returns a verdict *key* rather than a sentence - the client owns the wording
    so the page can be shown in any language.
    """
    taste = _avg([r.get("stars") for r in liked])
    skill = float(profile.target_stars)
    delta = taste - skill
    if not liked:
        verdict = ""
    elif delta < -0.4:
        verdict = "easier"
    elif delta > 0.4:
        verdict = "harder"
    else:
        verdict = "onpar"
    return {"skill_stars": round(skill, 2), "taste_stars": round(taste, 2),
            "delta": round(delta, 2), "verdict": verdict}


def _recent_likes(liked: list[dict], limit: int = 8) -> list[dict]:
    """Most recent likes first (get_ratings returns oldest-first)."""
    out = []
    for r in reversed(liked):
        if not r.get("title"):
            continue  # rated before we started storing artwork
        bid = r.get("beatmap_id")
        out.append({
            "set_id": r.get("set_id"),
            "title": r.get("title"),
            "artist": r.get("artist") or "",
            "cover_url": r.get("cover_url") or "",
            "stars": round(float(r.get("stars") or 0), 2),
            "url": f"https://osu.ppy.sh/b/{bid}" if bid
                   else f"https://osu.ppy.sh/beatmapsets/{r.get('set_id')}",
        })
        if len(out) >= limit:
            break
    return out


def _taste_summary(liked: list[dict], profile) -> dict | None:
    """The parts of the "your taste" line, for the client to phrase and translate."""
    if len(liked) < 3:
        return None
    bpm = _avg([r.get("bpm") for r in liked])
    stars = _avg([r.get("stars") for r in liked])
    genre_counts: dict[int, int] = {}
    for r in liked:
        gid = r.get("genre_id")
        if gid:
            genre_counts[int(gid)] = genre_counts.get(int(gid), 0) + 1
    top_genre_id = max(genre_counts, key=genre_counts.get) if genre_counts else 0

    tempo = ("chill" if bpm < 140 else "mid" if bpm < 170
             else "fast" if bpm < 200 else "breakneck")
    return {
        "tempo": tempo,
        "genre_id": top_genre_id,
        "genre_name": genre_name(top_genre_id) or "",
        "stars": round(stars, 1),
        "bpm": round(bpm),
    }


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.db = Database(config.database.dsn)
        await app.state.db.connect()
        app.state.store = Store(app.state.db)
        await migrate_json_file(app.state.store)  # one-time import of legacy data.json
        app.state.api = OsuApi(config.api.client_id, config.api.client_secret)
        app.state.reco = Recommender(
            app.state.api, PpCalculator(), app.state.store,
            star_window=config.bot.star_window,
            candidate_pages=config.bot.candidate_pages,
        )
        log.info("spotiosu web ready at %s", config.web.public_base)
        yield
        await app.state.api.close()
        await app.state.db.close()

    app = FastAPI(title="spotiosu", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(config),
        same_site="lax",              # required: osu! redirects back cross-site
        https_only=config.web.secure_cookies,
        max_age=14 * 24 * 3600,
    )

    def _require_user(request: Request) -> dict:
        user = request.session.get("user")
        if not user:
            raise HTTPException(status_code=401, detail="not logged in")
        return user

    # ---- auth ---------------------------------------------------------------
    @app.get("/login")
    async def login(request: Request):
        state = secrets.token_urlsafe(16)
        request.session["oauth_state"] = state
        return RedirectResponse(
            oauth.authorize_url(config.api.client_id, config.web.redirect_uri, state)
        )

    @app.get("/auth/callback")
    async def auth_callback(request: Request, code: str = "", state: str = "",
                            error: str = ""):
        if error:
            return RedirectResponse("/?error=denied")
        if not code or state != request.session.get("oauth_state"):
            return RedirectResponse("/?error=state")
        request.session.pop("oauth_state", None)
        try:
            token = await oauth.exchange_code(
                config.api.client_id, config.api.client_secret, code,
                config.web.redirect_uri,
            )
            user = await oauth.fetch_me(token)
        except Exception:  # noqa: BLE001
            log.exception("OAuth callback failed")
            return RedirectResponse("/?error=oauth")
        request.session["user"] = user
        return RedirectResponse("/")

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    # ---- state / onboarding -------------------------------------------------
    @app.get("/api/state")
    async def api_state(request: Request):
        user = request.session.get("user")
        if not user:
            return JSONResponse({"user": None})
        store = request.app.state.store
        uname = user["username"]
        return {
            "user": user,
            "onboarded": await store.is_onboarded(uname),
            "genres": await store.get_genres(uname),
            "rated_count": await store.count_ratings(uname),
            "onboarding_target": ONBOARDING_TARGET,
            "all_genres": genre_list(),
            "donate_url": config.web.donate_url,
        }

    @app.post("/api/onboarding/genres")
    async def api_set_genres(request: Request):
        user = _require_user(request)
        body = await request.json()
        genres = [int(g) for g in (body.get("genres") or [])]
        if len(genres) < 2:
            raise HTTPException(status_code=400, detail="pick at least 2 genres")
        await request.app.state.store.set_genres(user["username"], genres)
        request.app.state.reco.invalidate_profile(user["username"])
        return {"ok": True, "genres": genres}

    @app.get("/api/onboarding/tracks")
    async def api_onboarding_tracks(request: Request, count: int = 12):
        user = _require_user(request)
        store, reco = request.app.state.store, request.app.state.reco
        uname = user["username"]
        genres = await store.get_genres(uname)
        if not genres:
            raise HTTPException(status_code=400, detail="pick genres first")
        tracks = await reco.sample_for_onboarding(
            uname, user["id"], user.get("mode", "osu"), genres,
            count=max(1, min(count, 24)),
        )
        return {"items": [t.to_dict() for t in tracks]}

    @app.post("/api/onboarding/complete")
    async def api_onboarding_complete(request: Request):
        user = _require_user(request)
        await request.app.state.store.set_onboarded(user["username"], True)
        request.app.state.reco.invalidate_profile(user["username"])
        return {"ok": True}

    @app.post("/api/reonboard")
    async def api_reonboard(request: Request):
        user = _require_user(request)
        await request.app.state.store.reset_onboarding(user["username"])
        request.app.state.reco.invalidate_profile(user["username"])
        return {"ok": True}

    # ---- rating -------------------------------------------------------------
    @app.post("/api/rate")
    async def api_rate(request: Request):
        user = _require_user(request)
        body = await request.json()
        action = body.get("action")
        if action not in ("like", "dislike"):
            raise HTTPException(status_code=400, detail="action must be like|dislike")
        liked = action == "like"
        uname = user["username"]
        store, reco = request.app.state.store, request.app.state.reco

        creator = (body.get("creator") or "").strip()
        set_id = int(body.get("set_id") or 0)
        stars = float(body.get("stars") or 0)

        await store.add_rating(uname, {
            "set_id": set_id,
            "beatmap_id": int(body.get("beatmap_id") or 0),
            "genre_id": int(body.get("genre_id") or 0),
            "stars": stars,
            "bpm": float(body.get("bpm") or 0),
            "creator": creator,
            "liked": liked,
            # Stored so the profile page can show artwork without re-querying osu!.
            "title": body.get("title"),
            "artist": body.get("artist"),
            "cover_url": body.get("cover_url"),
        })
        # Keep the legacy like-weights in sync (shared with the CLI bot).
        if creator:
            await store.adjust_like(uname, f"mapper:{creator}", 0.4 if liked else -0.5)
        if liked and stars:
            await store.adjust_like(uname, "_star_sum", stars)
            await store.adjust_like(uname, "_star_cnt", 1)
        if set_id:
            await store.mark_seen(uname, set_id)  # never show the same map twice

        reco.invalidate_profile(uname)  # recommendations rebuild from here on
        return {"ok": True, "rated_count": await store.count_ratings(uname)}

    # ---- recommendations ----------------------------------------------------
    @app.get("/api/feed")
    async def api_feed(request: Request, count: int = 10, mods: str = "",
                       min_stars: float | None = None, max_stars: float | None = None):
        user = _require_user(request)
        reco = request.app.state.reco
        uname = user["username"]
        recs = await reco.recommend_many(
            uname, user["id"], user.get("mode", "osu"), mods or None,
            count=max(1, min(count, 30)),
            min_stars=min_stars, max_stars=max_stars,
        )
        profile = await reco.build_profile(user["id"], user.get("mode", "osu"), uname)
        return {
            "items": [r.to_dict() for r in recs],
            "suggested": {
                "min": round(profile.star_low, 2),
                "max": round(profile.star_high, 2),
                "target": profile.target_stars,
            },
            "genre_weights": {str(k): v for k, v in profile.genre_weights.items()},
        }

    @app.get("/api/profile/stats")
    async def api_profile_stats(request: Request):
        """Everything the profile page shows, aggregated from the rating history."""
        user = _require_user(request)
        store, reco = request.app.state.store, request.app.state.reco
        uname, mode = user["username"], user.get("mode", "osu")

        ratings = await store.get_ratings(uname)
        profile = await reco.build_profile(user["id"], mode, uname)
        liked = [r for r in ratings if r.get("liked")]

        return {
            "totals": _totals(ratings, liked),
            "genres": _genre_breakdown(ratings, profile.genre_weights),
            "difficulty": _difficulty_histogram(liked),
            "mappers": _top_mappers(liked),
            "skill_vs_taste": _skill_vs_taste(profile, liked),
            "recent_likes": _recent_likes(liked),
            "summary": _taste_summary(liked, profile),
        }

    @app.post("/api/reset")
    async def api_reset(request: Request):
        user = _require_user(request)
        await request.app.state.store.reset_seen(user["username"])
        request.app.state.reco.invalidate_profile(user["username"])
        return {"ok": True}

    # ---- static / index -----------------------------------------------------
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        # Browsers request /favicon.ico from the site root on their own, whatever
        # the <link> tags say - serving it here keeps that from 404-ing.
        return FileResponse(STATIC_DIR / "favicon" / "favicon.ico")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
