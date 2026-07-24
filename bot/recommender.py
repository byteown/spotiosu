"""The recommendation engine.

Builds a "taste profile" from a player's top plays, searches ranked beatmaps
around that profile, scores candidates by similarity + personal like/dislike
history, and returns a single recommendation with computed pp values.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .genres import genre_name
from .osu_api import MODE_TO_RULESET, OsuApi
from .pp import DEFAULT_ACCS, PpCalculator, format_mods, parse_mods
from .store import Store

log = logging.getLogger("spotiosu.reco")

# Scoring weights.
W_STAR = 1.0
W_BPM = 0.35
W_MAPPER = 0.5
# Popularity is a tie-breaker, not a driver: it only nudges away from the truly
# dead uploads (broken audio, unfinished maps) that come with searching the whole
# listing. At 0.1 against W_GENRE 1.2 it can never outrank musical taste.
W_POP = 0.1
W_GENRE = 1.2   # music taste is the primary signal once onboarding is done
JITTER = 0.15

# How strongly a single like/dislike moves that genre's weight.
GENRE_PICK_BONUS = 0.6
GENRE_LIKE = 0.35
GENRE_DISLIKE = -0.45

# ---- searching the whole listing -------------------------------------------
# The osu! search endpoint only ever hands back the first ~100 results of a given
# ordering, so a *fixed* query means a fixed pool: the same most-played maps every
# time, and once the user has seen them the feed is permanently empty. Instead we
# probe random slices - a random ordering over a random upload year - which turn
# out to be near-disjoint (measured: 0/50 overlap between most sorts, 0/50 between
# years), so the reachable pool is effectively the entire listing.
SEARCH_SORTS = [
    "plays_desc", "favourites_desc", "rating_desc", "ranked_desc",
    "updated_desc", "difficulty_desc", "difficulty_asc", "relevance_desc",
    "artist_asc", "title_asc",
]
# "any" also covers loved and graveyard maps, which is where most of the *music*
# lives - for the thin genres it is the difference between a dead end and a real
# catalogue (Classical: 90 ranked sets vs 674 across all statuses).
SEARCH_STATUS = "any"
# ...with one exception: "work in progress" means the map genuinely is not
# finished, so recommending it wastes the user's rating.
SKIP_STATUSES = {"wip"}
FIRST_YEAR = 2008           # nothing meaningful was uploaded before this
PROBES_PER_GENRE = 2
GENERAL_PROBES = 2          # untagged passes keep some serendipity
MAX_CONCURRENT_SEARCHES = 6


@dataclass
class TasteProfile:
    mode: str
    target_stars: float
    star_low: float
    star_high: float
    bpm_mean: float
    fav_mappers: set[str] = field(default_factory=set)
    played_set_ids: set[int] = field(default_factory=set)
    pp_level: float = 0.0
    # genre id -> preference weight, roughly [-1, 1.5]. Built from the onboarding
    # questionnaire and every like/dislike the user has given since.
    genre_weights: dict[int, float] = field(default_factory=dict)


@dataclass
class Recommendation:
    beatmap_id: int
    set_id: int
    artist: str
    title: str
    version: str
    creator: str
    stars: float
    bpm: float
    length: int          # seconds
    mods: list[str]
    pp: dict[float, float] | None  # {accuracy: pp} or None if unavailable
    url: str
    cover_url: str = ""
    preview_url: str = ""  # 30s audio preview mp3
    genre_id: int = 0
    genre_name: str = ""

    def to_dict(self) -> dict:
        return {
            "beatmap_id": self.beatmap_id,
            "set_id": self.set_id,
            "artist": self.artist,
            "title": self.title,
            "version": self.version,
            "creator": self.creator,
            "stars": self.stars,
            "bpm": self.bpm,
            "length": self.length,
            "length_str": _fmt_len(self.length) if self.length else "",
            "mods": self.mods,
            "pp": {str(int(k)): v for k, v in (self.pp or {}).items()},
            "url": self.url,
            "cover_url": self.cover_url,
            "preview_url": self.preview_url,
            "genre_id": self.genre_id,
            "genre_name": self.genre_name,
        }


def _cover_of(bset: dict) -> str:
    covers = bset.get("covers") or {}
    return covers.get("cover@2x") or covers.get("cover") or covers.get("card") or ""


def _preview_of(bset: dict) -> str:
    url = bset.get("preview_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    return url


def _genre_of(bset: dict) -> int:
    genre = bset.get("genre")
    if isinstance(genre, dict) and genre.get("id"):
        return int(genre["id"])
    if isinstance(genre, int):
        return genre
    return int(bset.get("genre_id") or 0)


def _gauss(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x == mu else 0.0
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def _fmt_len(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


class Recommender:
    def __init__(self, api: OsuApi, pp: PpCalculator, store: Store, *,
                 star_window: float = 0.7, candidate_pages: int = 1,
                 profile_ttl: float = 300.0, seen_limit: int = 5000) -> None:
        self._api = api
        self._pp = pp
        self._store = store
        self._star_window = star_window
        self._candidate_pages = candidate_pages
        self._profile_ttl = profile_ttl
        # How many past sets to remember per user. Anything the list forgets can
        # be recommended again, so it has to comfortably outlast normal use.
        self._seen_limit = seen_limit
        self._profile_cache: dict[str, tuple[float, TasteProfile]] = {}
        # pp for a given (beatmap, mods) never changes, so it is worth keeping:
        # the .osu download behind it costs ~0.5s and is shared across users.
        self._pp_cache: OrderedDict[tuple[int, int], dict[float, float] | None] = OrderedDict()
        self._pp_cache_max = 1000

    def invalidate_profile(self, username: str) -> None:
        self._profile_cache.pop(username.lower(), None)

    # ---- profile ------------------------------------------------------------
    async def build_profile(self, user_id: int, mode: str, username: str) -> TasteProfile:
        # Serve a recently-built profile to keep repeat !r calls fast (the top-plays
        # fetch is the slowest part). Feedback via invalidate_profile() drops it.
        cached = self._profile_cache.get(username.lower())
        if cached and (time.monotonic() - cached[0]) < self._profile_ttl:
            return cached[1]

        best = await self._api.get_user_best(user_id, mode=mode, limit=100)
        likes = await self._store.get_likes(username)

        stars: list[float] = []
        weights: list[float] = []
        bpms: list[float] = []
        mappers: dict[str, float] = {}
        played_sets: set[int] = set()
        pp_values: list[float] = []

        for score in best:
            bm = score.get("beatmap") or {}
            bset = score.get("beatmapset") or {}
            sr = bm.get("difficulty_rating")
            if sr is None:
                continue
            w = float(score.get("pp") or 1.0)
            stars.append(float(sr))
            weights.append(w)
            if bm.get("bpm"):
                bpms.append(float(bm["bpm"]))
            creator = (bset.get("creator") or "").strip()
            if creator:
                mappers[creator] = mappers.get(creator, 0.0) + w
            if bset.get("id"):
                played_sets.add(int(bset["id"]))
            if score.get("pp"):
                pp_values.append(float(score["pp"]))

        if stars:
            total_w = sum(weights) or 1.0
            target = sum(s * w for s, w in zip(stars, weights)) / total_w
            bpm_mean = (sum(bpms) / len(bpms)) if bpms else 160.0
        else:
            # New / private player: sane defaults.
            target, bpm_mean = 3.0, 160.0

        # Blend in liked-map star preference, if the user has given feedback.
        liked_cnt = likes.get("_star_cnt", 0.0)
        if liked_cnt > 0:
            liked_avg = likes.get("_star_sum", 0.0) / liked_cnt
            target = 0.65 * target + 0.35 * liked_avg

        fav_mappers = {m for m, _ in sorted(mappers.items(), key=lambda kv: -kv[1])[:8]}
        pp_level = (sum(pp_values[:10]) / min(len(pp_values), 10)) if pp_values else 0.0

        genre_weights = await self._genre_weights(username)

        # Songs the user actually liked steer BPM; difficulty stays tied to skill.
        liked_bpms = [
            float(r["bpm"]) for r in await self._store.get_ratings(username)
            if r.get("liked") and r.get("bpm")
        ]
        if liked_bpms:
            bpm_mean = 0.5 * bpm_mean + 0.5 * (sum(liked_bpms) / len(liked_bpms))

        profile = TasteProfile(
            mode=mode,
            target_stars=round(target, 2),
            star_low=max(0.0, target - self._star_window),
            star_high=target + self._star_window,
            bpm_mean=bpm_mean,
            fav_mappers=fav_mappers,
            played_set_ids=played_sets,
            pp_level=round(pp_level, 1),
            genre_weights=genre_weights,
        )
        self._profile_cache[username.lower()] = (time.monotonic(), profile)
        return profile

    async def _genre_weights(self, username: str) -> dict[int, float]:
        """Genres the user picked at onboarding, then continuously corrected by
        every like/dislike they give afterwards."""
        weights: dict[int, float] = {}
        for gid in await self._store.get_genres(username):
            weights[int(gid)] = weights.get(int(gid), 0.0) + GENRE_PICK_BONUS
        for rating in await self._store.get_ratings(username):
            gid = int(rating.get("genre_id") or 0)
            if not gid:
                continue
            delta = GENRE_LIKE if rating.get("liked") else GENRE_DISLIKE
            weights[gid] = weights.get(gid, 0.0) + delta
        return {g: max(-1.0, min(1.5, w)) for g, w in weights.items()}

    async def preferred_genres(self, username: str, limit: int = 3) -> list[int]:
        """The genres to search, strongest first.

        Sampled by weight rather than taken strictly top-N: someone who likes five
        genres should not be shown only three of them forever. The strongest are
        still by far the likeliest to come up.
        """
        weights = await self._genre_weights(username)
        positive = [(g, w) for g, w in weights.items() if w > 0]
        positive.sort(key=lambda kv: -kv[1])
        if len(positive) <= limit:
            return [g for g, _ in positive]

        chosen: list[int] = []
        pool = list(positive)
        for _ in range(limit):
            total = sum(w for _g, w in pool)
            cut = random.uniform(0, total)
            acc = 0.0
            for i, (g, w) in enumerate(pool):
                acc += w
                if acc >= cut:
                    chosen.append(g)
                    pool.pop(i)
                    break
        return chosen

    # ---- recommendation -----------------------------------------------------
    async def _scored_candidates(
        self, username: str, user_id: int, mode: str, acronyms: list[str],
        min_stars: float | None = None, max_stars: float | None = None,
        need: int = 1,
    ) -> tuple[TasteProfile, list[tuple[float, dict, dict]]]:
        profile = await self.build_profile(user_id, mode, username)

        # Difficulty-increasing mods inflate star rating; aim a touch lower so the
        # *base* map sits in a comfortable range once mods are applied.
        star_shift = 0.0
        if "DT" in acronyms or "NC" in acronyms:
            star_shift -= 0.5
        if "HR" in acronyms:
            star_shift -= 0.2
        low = max(0.0, profile.star_low + star_shift)
        high = profile.star_high + star_shift
        # An explicit difficulty filter from the UI wins over the skill-derived band.
        if min_stars is not None:
            low = max(0.0, float(min_stars))
        if max_stars is not None:
            high = float(max_stars)
        if high < low:
            low, high = high, low

        # An explicit filter is a promise to the user: honour it exactly, instead of
        # the small tolerance we allow when the band is only inferred from skill.
        explicit = min_stars is not None or max_stars is not None
        tolerance = 0.0 if explicit else 0.3
        genres = await self.preferred_genres(username)
        seen = await self._store.get_seen(username)
        likes = await self._store.get_likes(username)

        scored: list[tuple[float, dict, dict]] = []
        picked: set[int] = set()

        async def harvest(lo: float, hi: float) -> None:
            for bset, bm in await self._gather_candidates(
                profile, mode, lo, hi, genres, tolerance
            ):
                set_id = int(bset["id"])
                if set_id in picked or set_id in seen or set_id in profile.played_set_ids:
                    continue
                picked.add(set_id)
                scored.append((self._score(bset, bm, profile, likes), bset, bm))

        await harvest(low, high)

        # Running dry is a search problem, not a "there are no maps" problem: the
        # probes are random, so simply drawing again lands on different slices of
        # the listing. Only if that still fails do we loosen the difficulty band -
        # and never when the user set one explicitly.
        if len(scored) < need:
            await harvest(low, high)
        if len(scored) < need and not explicit:
            for extra in (0.8, 2.0):
                await harvest(max(0.0, low - extra), high + extra)
                if len(scored) >= need:
                    break

        scored.sort(key=lambda t: -t[0])
        return profile, scored

    def _make_rec(
        self, bset: dict, bm: dict, profile: TasteProfile,
        acronyms: list[str], pp: dict[float, float] | None,
    ) -> Recommendation:
        beatmap_id = int(bm["id"])
        return Recommendation(
            beatmap_id=beatmap_id,
            set_id=int(bset["id"]),
            artist=bset.get("artist", "?"),
            title=bset.get("title", "?"),
            version=bm.get("version", ""),
            creator=bset.get("creator", "?"),
            stars=round(float(bm.get("difficulty_rating", profile.target_stars)), 2),
            bpm=float(bm.get("bpm") or bset.get("bpm") or 0),
            length=int(bm.get("total_length") or 0),
            mods=acronyms,
            pp=pp,
            url=f"https://osu.ppy.sh/b/{beatmap_id}",
            cover_url=_cover_of(bset),
            preview_url=_preview_of(bset),
            genre_id=_genre_of(bset),
            genre_name=genre_name(_genre_of(bset)),
        )

    async def recommend(
        self, username: str, user_id: int, mode: str, mod_string: str | None = None
    ) -> Recommendation | None:
        acronyms, mods_bits = parse_mods(mod_string)
        profile, scored = await self._scored_candidates(username, user_id, mode, acronyms)
        if not scored:
            return None
        _, bset, bm = scored[0]
        await self._store.mark_seen(username, int(bset["id"]), limit=self._seen_limit)
        pp = await self._compute_pp(int(bm["id"]), mods_bits)
        rec = self._make_rec(bset, bm, profile, acronyms, pp)
        await self._store.set_last(username, {
            "beatmap_id": rec.beatmap_id, "set_id": rec.set_id,
            "stars": rec.stars, "creator": rec.creator,
            "artist": rec.artist, "title": rec.title, "version": rec.version,
        })
        return rec

    async def recommend_many(
        self, username: str, user_id: int, mode: str,
        mod_string: str | None = None, count: int = 15,
        min_stars: float | None = None, max_stars: float | None = None,
        with_pp: bool = False,
    ) -> list[Recommendation]:
        """Return a ranked queue of recommendations (for the web player).

        pp is left out by default: computing it for the whole batch means one .osu
        download per map (~0.5s each) and dominated the response time, even though
        the player shows one track at a time and the user may never reach most of
        them. The client fetches pp per track from /api/pp instead.
        """
        acronyms, mods_bits = parse_mods(mod_string)
        profile, scored = await self._scored_candidates(
            username, user_id, mode, acronyms, min_stars, max_stars, need=count
        )
        picks = scored[:count]
        await self._store.mark_seen_many(
            username, [int(bset["id"]) for _, bset, _bm in picks],
            limit=self._seen_limit,
        )

        pps: list[dict[float, float] | None] = [None] * len(picks)
        if with_pp:
            sem = asyncio.Semaphore(8)

            async def pp_for(bm: dict) -> dict[float, float] | None:
                async with sem:
                    return await self.compute_pp(int(bm["id"]), mods_bits)

            pps = list(await asyncio.gather(*(pp_for(bm) for _, _bset, bm in picks)))

        recs = [
            self._make_rec(bset, bm, profile, acronyms, pp)
            for (_, bset, bm), pp in zip(picks, pps)
        ]
        if recs:
            top = recs[0]
            await self._store.set_last(username, {
                "beatmap_id": top.beatmap_id, "set_id": top.set_id,
                "stars": top.stars, "creator": top.creator,
                "artist": top.artist, "title": top.title, "version": top.version,
            })
        return recs

    # ---- onboarding ---------------------------------------------------------
    async def sample_for_onboarding(
        self, username: str, user_id: int, mode: str,
        genres: list[int], count: int = 12,
    ) -> list[Recommendation]:
        """Popular songs from the genres the user picked, for the taste questionnaire.

        Difficulty is not filtered here - we are asking about *music*, so we take
        well-known maps and simply show the difficulty closest to their skill.
        """
        profile = await self.build_profile(user_id, mode, username)
        base: dict = {}
        ruleset = MODE_TO_RULESET.get(mode)
        if ruleset is not None:
            base["m"] = ruleset

        # Ranked + most-played on purpose: the quiz asks "do you like this song?",
        # which only works with music the user has a chance of recognising. Two
        # pages so retaking the quiz does not replay the same ten tracks.
        genres = genres or [3, 10]  # sane default if somehow empty
        results = await asyncio.gather(
            *(self._search_pages("", dict(base, g=g), pages=2) for g in genres),
            return_exceptions=True,
        )

        # Round-robin across genres so every picked genre is represented.
        per_genre: list[list[Recommendation]] = []
        used: set[int] = set()
        rated = {r.get("set_id") for r in await self._store.get_ratings(username)}
        for res in results:
            if isinstance(res, BaseException):
                log.warning("onboarding search failed: %s", res)
                continue
            bucket: list[Recommendation] = []
            pool = list(res)
            random.shuffle(pool)
            for bset in pool:
                sid = int(bset.get("id") or 0)
                if not sid or sid in used or sid in rated:
                    continue
                if not _preview_of(bset):
                    continue  # a song we cannot play is useless for rating
                bm = self._pick_closest_difficulty(bset, profile.target_stars, mode)
                if bm is None:
                    continue
                used.add(sid)
                bucket.append(self._make_rec(bset, bm, profile, [], None))
            per_genre.append(bucket)

        out: list[Recommendation] = []
        for i in range(count):
            for bucket in per_genre:
                if i < len(bucket) and len(out) < count:
                    out.append(bucket[i])
            if len(out) >= count:
                break
        return out[:count]

    @staticmethod
    def _pick_closest_difficulty(bset: dict, target: float, mode: str) -> dict | None:
        best_bm, best_dist = None, 1e9
        for bm in bset.get("beatmaps", []):
            if bm.get("mode") and bm["mode"] != mode:
                continue
            sr = bm.get("difficulty_rating")
            if sr is None:
                continue
            dist = abs(float(sr) - target)
            if dist < best_dist:
                best_dist, best_bm = dist, bm
        return best_bm

    @staticmethod
    def _probes(genres: list[int], base: dict) -> list[tuple[dict, int | None]]:
        """Pick which slices of the listing to search this time round.

        Each probe is (search params, upload year or None). The first probe for a
        genre is unsliced so its strongest maps stay reachable; the rest are
        pinned to a random year, which is what actually breaks us out of the
        "first 100 by playcount" trap.
        """
        years = list(range(FIRST_YEAR, time.gmtime().tm_year + 1))

        def slice_for(genre: int | None, first: bool) -> tuple[dict, int | None]:
            extra = dict(base, s=SEARCH_STATUS, sort=random.choice(SEARCH_SORTS))
            if genre is not None:
                extra["g"] = genre
            return extra, (None if first else random.choice(years))

        probes: list[tuple[dict, int | None]] = []
        for genre in genres:
            for i in range(PROBES_PER_GENRE):
                probes.append(slice_for(genre, first=i == 0))
        for i in range(GENERAL_PROBES):
            probes.append(slice_for(None, first=i == 0))
        return probes

    async def _gather_candidates(
        self, profile: TasteProfile, mode: str, low: float, high: float,
        genres: list[int] | None = None, tolerance: float = 0.3,
    ) -> list[tuple[dict, dict]]:
        """Collect candidate (beatmapset, difficulty) pairs.

        Runs several probes per preferred genre so the user's taste drives what
        shows up, plus untagged passes for variety. See SEARCH_SORTS on why the
        probes are randomised rather than a single fixed query.
        """
        band = f"stars>{low:.2f} stars<{high:.2f}"
        base: dict = {}
        ruleset = MODE_TO_RULESET.get(mode)
        if ruleset is not None:
            base["m"] = ruleset

        sem = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

        async def probe(extra: dict, year: int | None) -> list[dict]:
            query = band if year is None else f"{band} created={year}"
            async with sem:
                return await self._search_pages(query, extra)

        results = await asyncio.gather(
            *(probe(extra, year) for extra, year in self._probes(genres or [], base)),
            return_exceptions=True,
        )

        out: list[tuple[dict, dict]] = []
        seen_sets: set[int] = set()
        for res in results:
            if isinstance(res, BaseException):
                log.warning("candidate search failed: %s", res)
                continue
            for bset in res:
                sid = int(bset.get("id") or 0)
                if not sid or sid in seen_sets:
                    continue
                if bset.get("status") in SKIP_STATUSES:
                    continue
                # Widening to every status turns up the occasional set with no
                # audio; in a music player that is not a recommendation at all.
                if not _preview_of(bset):
                    continue
                bm = self._pick_difficulty(
                    bset, profile.target_stars, mode, low, high, tolerance
                )
                if bm is not None:
                    seen_sets.add(sid)
                    out.append((bset, bm))
        return out

    async def _search_pages(self, query: str, extra: dict, pages: int = 0) -> list[dict]:
        """Follow cursor pagination for one search, up to `pages` (default: config)."""
        sets: list[dict] = []
        cursor: str | None = None
        for _ in range(pages or self._candidate_pages):
            page_extra = dict(extra)
            if cursor:
                page_extra["cursor_string"] = cursor
            data = await self._api.search_beatmapsets(query, page_extra)
            sets.extend(data.get("beatmapsets", []))
            cursor = data.get("cursor_string")
            if not cursor:
                break
        return sets

    @staticmethod
    def _pick_difficulty(
        bset: dict, target: float, mode: str, low: float, high: float,
        tolerance: float = 0.3,
    ) -> dict | None:
        """From a beatmapset, choose the diff closest to target within the band."""
        best_bm = None
        best_dist = 1e9
        for bm in bset.get("beatmaps", []):
            if bm.get("mode") and bm["mode"] != mode:
                continue
            sr = bm.get("difficulty_rating")
            if sr is None or not (low - tolerance <= sr <= high + tolerance):
                continue
            dist = abs(sr - target)
            if dist < best_dist:
                best_dist, best_bm = dist, bm
        return best_bm

    def _score(self, bset: dict, bm: dict, profile: TasteProfile, likes: dict) -> float:
        sr = float(bm.get("difficulty_rating", profile.target_stars))
        bpm = float(bm.get("bpm") or bset.get("bpm") or profile.bpm_mean)
        creator = (bset.get("creator") or "").strip()
        play_count = float(bset.get("play_count") or bm.get("playcount") or 0)

        star_sigma = max(0.4, self._star_window / 1.5)
        s = W_STAR * _gauss(sr, profile.target_stars, star_sigma)
        s += W_BPM * _gauss(bpm, profile.bpm_mean, 40.0)
        if creator in profile.fav_mappers:
            s += W_MAPPER
        s += W_MAPPER * max(-1.0, min(1.0, likes.get(f"mapper:{creator}", 0.0)))
        s += W_POP * min(1.0, math.log10(play_count + 1) / 6.0)
        s += W_GENRE * profile.genre_weights.get(_genre_of(bset), 0.0)
        s += random.uniform(0, JITTER)
        return s

    async def compute_pp(self, beatmap_id: int, mods_bits: int) -> dict[float, float] | None:
        """pp at the standard accuracies, memoised (the result is deterministic)."""
        if not self._pp.available:
            return None
        key = (int(beatmap_id), int(mods_bits))
        if key in self._pp_cache:
            self._pp_cache.move_to_end(key)
            return self._pp_cache[key]

        osu_text = await self._api.download_osu_file(beatmap_id)
        result = self._pp.compute(osu_text, mods_bits, DEFAULT_ACCS) if osu_text else None

        self._pp_cache[key] = result
        if len(self._pp_cache) > self._pp_cache_max:
            self._pp_cache.popitem(last=False)
        return result

    # Kept for the existing internal call sites.
    _compute_pp = compute_pp

    # ---- /np : pp for an arbitrary beatmap ----------------------------------
    async def pp_for_beatmap(
        self, beatmap_id: int, mod_string: str | None = None
    ) -> Recommendation | None:
        acronyms, mods_bits = parse_mods(mod_string)
        bm = await self._api.get_beatmap(beatmap_id)
        if not bm:
            return None
        bset = bm.get("beatmapset") or {}
        pp = await self._compute_pp(beatmap_id, mods_bits)
        return Recommendation(
            beatmap_id=beatmap_id,
            set_id=int(bset.get("id") or bm.get("beatmapset_id") or 0),
            artist=bset.get("artist", "?"),
            title=bset.get("title", "?"),
            version=bm.get("version", ""),
            creator=bset.get("creator", "?"),
            stars=round(float(bm.get("difficulty_rating") or 0), 2),
            bpm=float(bm.get("bpm") or 0),
            length=int(bm.get("total_length") or 0),
            mods=acronyms,
            pp=pp,
            url=f"https://osu.ppy.sh/b/{beatmap_id}",
        )


def format_recommendation(rec: Recommendation, *, prefix: str = "") -> str:
    """Render a Recommendation as an osu! chat line with a clickable link.

    Example:
      Try: [https://osu.ppy.sh/b/75 Artist - Title [Diff]] | +HDDT | ★2.63 |
           173bpm | 1:30 | 95%:56 98%:82 99%:95 100%:113pp
    """
    link = f"[{rec.url} {rec.artist} - {rec.title} [{rec.version}]]"
    fields = [format_mods(rec.mods), f"★{rec.stars:.2f}"]
    if rec.bpm:
        fields.append(f"{rec.bpm:.0f}bpm")
    if rec.length:
        fields.append(_fmt_len(rec.length))
    if rec.pp:
        fields.append(
            " ".join(f"{int(acc)}%:{rec.pp[acc]:.0f}" for acc in DEFAULT_ACCS if acc in rec.pp)
            + "pp"
        )
    else:
        fields.append("pp: n/a")
    head = f"{prefix} {link}" if prefix else link
    return head + " | " + " | ".join(fields)
