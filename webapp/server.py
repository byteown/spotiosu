"""FastAPI backend for the spotiosu web app.

Flow:
  1. Sign in with osu! (OAuth).
  2. First visit -> onboarding: pick genres, then rate sample tracks like/dislike.
  3. Main player: one map at a time, rating is mandatory and rebuilds the profile.

Reuses the recommendation engine from `bot/` (osu_api, recommender, pp, store).
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from bot.config import Config
from bot.db import Database
from bot.genres import genre_list
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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
