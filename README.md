# spotiosu 🎵

A personal **osu! beatmap recommendation website**. Sign in with your osu! account
and get a Spotify-like feed of ranked maps tuned to *your* top plays — with cover
art, 30-second audio previews, real pp values, and 👍/👎 to refine your taste.

Built for the "I don't know what to play" moment.

## How it works

**First visit — a short taste quiz:**
1. **Sign in with osu!** (OAuth).
2. **Pick your genres** (at least 2) — Anime, Electronic, Rock, Metal, …
3. **Rate 10 songs** from those genres in the player: 👍 or 👎. This is what teaches
   the system your *music* taste.

**After that — the player:** one map at a time, full-screen, with its audio preview
playing. Rating is **mandatory** — 👍/👎 is how you move to the next track, and every
rating rebuilds your profile, so recommendations shift as you go.

**What drives a recommendation:**
- **Genre** — the strongest signal, from your quiz answers and every rating since.
- **Difficulty** — from your osu! top plays, so maps stay at your skill level
  (or override it with the difficulty filter).
- **BPM / mappers** — learned from what you like.
- Maps you've played or already seen are skipped; pp is computed locally.

## Setup

### 1. Register an osu! OAuth app (free)
1. Go to <https://osu.ppy.sh/home/account/edit> → **OAuth** → **New OAuth Application**.
2. Name it `spotiosu`.
3. **Application Callback URL** — set it **exactly** to:
   ```
   http://localhost:8000/auth/callback
   ```
   > ⚠️ This must match exactly, or sign-in fails with a redirect error.
4. Copy the **Client ID** and **Client secret** into `config.json`:
   ```json
   { "osu_api": { "client_id": 12345, "client_secret": "SECRET" } }
   ```

### 2. Run
```powershell
.\run_web.ps1
```
First run creates a virtualenv and installs dependencies. Then open
<http://localhost:8000> and click **Sign in with osu!**.

## Using it
- **👍 / 👎** — required to advance; also retrains your recommendations.
- **Keyboard:** `←` dislike · `→` like · `space` play/pause.
- **Difficulty filter** — "Match my skill" follows your top plays; uncheck it and drag
  the sliders for an exact ★ range (respected strictly).
- **Mods** (e.g. `HDDT`) — targets maps that play well with them; pp is computed with them.
- **Preview volume starts at 20%** every time, adjustable with the slider.
- **Open in osu!** opens the beatmap page. **Reset history** lets older maps return.
- **Retake quiz** clears your genres/ratings and starts the questionnaire over.

Anyone can sign in with their own osu! account and gets their own personalised player.

## Project layout
```
webapp/
  server.py       FastAPI app: OAuth login, session, JSON API
  oauth.py        osu! "Sign in with osu!" (Authorization Code flow)
  static/         index.html, style.css, app.js  (the Spotify-like UI)
bot/              the recommendation engine (reused by the web app)
  osu_api.py      osu! API v2 client (data + .osu download)
  recommender.py  taste profile, genre weighting, candidate search, scoring
  genres.py       osu! genre ids used by the quiz and search
  pp.py           mod parsing + rosu-pp pp calculation
  store.py        JSON persistence (genres, ratings, seen maps) — atomic writes
  config.py       config.json loading
config.json       your credentials (gitignored)
```

## Also included: CLI modes (optional)
The same engine also runs as a terminal tool and a legacy osu! chat bot:
- `.\run.ps1 --console` — type `!r` in the terminal for your own recommendations.
- `.\run.ps1` — Tillerino-style IRC chat bot (note: unreliable with osu!lazer, which
  is why the website is the primary experience). Needs `osu_irc` creds in config.

## Notes
- `rosu-pp-py` is optional; without it recommendations still work but omit pp.
- The site is read-only toward osu! (public API + your identity). It never plays for you.
- Your session is a signed local cookie; the secret lives in `.session_secret` (gitignored).
