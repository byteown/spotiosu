"""Command parsing and message dispatch."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import Config
from .irc_client import IncomingMessage, IrcClient
from .osu_api import OsuApi
from .recommender import Recommender, format_recommendation
from .store import Store

log = logging.getLogger("spotiosu.cmd")

# Map Bancho's full mod names (as they appear in /np) to acronyms.
NP_MOD_NAMES = {
    "Hidden": "HD", "HardRock": "HR", "DoubleTime": "DT", "Nightcore": "NC",
    "HalfTime": "HT", "Easy": "EZ", "NoFail": "NF", "Flashlight": "FL",
    "SpunOut": "SO", "Perfect": "PF", "SuddenDeath": "SD",
}

HELP_TEXT = (
    "spotiosu commands: "
    "!r [mods] = recommend a map for your level · "
    "!with <mods> = redo last map with mods · "
    "!reset = clear recent history · "
    "/np = paste a now-playing to get its pp · "
    "!like / !dislike = tune your recommendations · !help"
)


@dataclass
class BotContext:
    config: Config
    api: OsuApi
    reco: Recommender
    store: Store
    # caches: irc nick -> resolved (user_id, mode)
    _user_cache: dict[str, tuple[int, str] | None]

    def __init__(self, config: Config, api: OsuApi, reco: Recommender, store: Store):
        self.config = config
        self.api = api
        self.reco = reco
        self.store = store
        self._user_cache = {}

    async def resolve_user(self, nick: str) -> tuple[int, str] | None:
        """Resolve an IRC nick to (osu user_id, preferred mode)."""
        key = nick.lower()
        if key in self._user_cache:
            return self._user_cache[key]
        mode = self.config.bot.default_mode
        # IRC replaces spaces with underscores; try both forms.
        candidates = [nick]
        if "_" in nick:
            candidates.append(nick.replace("_", " "))
        resolved: tuple[int, str] | None = None
        for cand in candidates:
            user = await self.api.get_user(cand, mode=mode)
            if user and user.get("id"):
                resolved = (int(user["id"]), user.get("playmode") or mode)
                break
        self._user_cache[key] = resolved
        return resolved


async def handle_message(msg: IncomingMessage, irc: IrcClient, ctx: BotContext) -> None:
    text = msg.text.strip()

    # /np and other ACTIONs → pp of the referenced map.
    if msg.is_action:
        await _handle_np(msg, irc, ctx, text)
        return

    prefix = ctx.config.bot.command_prefix
    if not text.startswith(prefix):
        # Be friendly: any plain message gets a hint.
        await irc.send_message(msg.sender, f"Type {prefix}help for commands.")
        return

    body = text[len(prefix):].strip()
    if not body:
        return
    cmd, _, rest = body.partition(" ")
    cmd = cmd.lower()
    rest = rest.strip()

    try:
        if cmd in ("r", "recommend", "rec"):
            await _handle_recommend(msg, irc, ctx, rest)
        elif cmd in ("with", "w"):
            await _handle_with(msg, irc, ctx, rest)
        elif cmd in ("reset",):
            await ctx.store.reset_seen(msg.sender)
            await irc.send_message(msg.sender, "Recommendation history cleared.")
        elif cmd in ("like", "l"):
            await _handle_feedback(msg, irc, ctx, like=True)
        elif cmd in ("dislike", "d"):
            await _handle_feedback(msg, irc, ctx, like=False)
        elif cmd in ("help", "h", "commands"):
            await irc.send_message(msg.sender, HELP_TEXT)
        else:
            await irc.send_message(msg.sender, f"Unknown command. {HELP_TEXT}")
    except Exception:  # noqa: BLE001 - always answer the user, never fail silently
        log.exception("Command '%s' from %s failed", cmd, msg.sender)
        await irc.send_message(
            msg.sender, "Sorry, something broke handling that. Try again in a moment."
        )


async def _handle_recommend(
    msg: IncomingMessage, irc: IrcClient, ctx: BotContext, rest: str
) -> None:
    # Instant acknowledgement: lazer users are only addressable over legacy IRC
    # for a short window after they message us, so a fast reply lands even if the
    # full recommendation (a few API calls later) may not.
    await irc.send_message(msg.sender, "\U0001f50d Looking for a map...")
    resolved = await ctx.resolve_user(msg.sender)
    if resolved is None:
        await irc.send_message(
            msg.sender, "Couldn't find your osu! profile from the API. Try again later."
        )
        return
    user_id, mode = resolved
    mods = rest or None
    try:
        rec = await ctx.reco.recommend(msg.sender, user_id, mode, mods)
    except Exception:  # noqa: BLE001
        log.exception("recommend failed for %s", msg.sender)
        await irc.send_message(msg.sender, "Something went wrong building a recommendation.")
        return
    if rec is None:
        await irc.send_message(
            msg.sender,
            "No fresh maps left in your range — try !reset, or different mods.",
        )
        return
    await irc.send_message(msg.sender, format_recommendation(rec, prefix="Try:"))


async def _handle_with(
    msg: IncomingMessage, irc: IrcClient, ctx: BotContext, rest: str
) -> None:
    last = await ctx.store.get_last(msg.sender)
    if not last:
        await irc.send_message(msg.sender, "No previous map. Use !r first.")
        return
    if not rest:
        await irc.send_message(msg.sender, "Usage: !with HDDT")
        return
    rec = await ctx.reco.pp_for_beatmap(int(last["beatmap_id"]), rest)
    if rec is None:
        await irc.send_message(msg.sender, "Couldn't recompute that map.")
        return
    await irc.send_message(msg.sender, format_recommendation(rec, prefix="With mods:"))


async def _handle_feedback(
    msg: IncomingMessage, irc: IrcClient, ctx: BotContext, *, like: bool
) -> None:
    last = await ctx.store.get_last(msg.sender)
    if not last:
        await irc.send_message(msg.sender, "Nothing to rate yet. Use !r first.")
        return
    creator = last.get("creator", "")
    star = float(last.get("stars") or 0)
    if like:
        await ctx.store.adjust_like(msg.sender, f"mapper:{creator}", 0.4)
        await ctx.store.adjust_like(msg.sender, "_star_sum", star)
        await ctx.store.adjust_like(msg.sender, "_star_cnt", 1)
        await irc.send_message(msg.sender, "Noted - I'll lean toward maps like that. \U0001f44d")
    else:
        await ctx.store.adjust_like(msg.sender, f"mapper:{creator}", -0.5)
        await ctx.store.mark_seen(msg.sender, int(last["set_id"]))  # never again
        await irc.send_message(msg.sender, "Got it - steering away from that. \U0001f44e")
    ctx.reco.invalidate_profile(msg.sender)  # feedback changes the taste profile


async def _handle_np(
    msg: IncomingMessage, irc: IrcClient, ctx: BotContext, text: str
) -> None:
    beatmap_id = _extract_beatmap_id(text)
    if beatmap_id is None:
        return  # not a beatmap ACTION; ignore silently
    mods = _extract_np_mods(text)
    rec = await ctx.reco.pp_for_beatmap(beatmap_id, mods)
    if rec is None:
        await irc.send_message(msg.sender, "Couldn't read that map.")
        return
    await irc.send_message(msg.sender, format_recommendation(rec, prefix="np:"))


# --- parsing helpers --------------------------------------------------------
_ID_PATTERNS = [
    re.compile(r"#(?:osu|taiko|fruits|mania)/(\d+)"),
    re.compile(r"/b/(\d+)"),
    re.compile(r"/beatmaps/(\d+)"),
    re.compile(r"/beatmapsets/\d+#(?:osu|taiko|fruits|mania)?/?(\d+)"),
]


def _extract_beatmap_id(text: str) -> int | None:
    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


def _extract_np_mods(text: str) -> str | None:
    # Mods trail the link as e.g. "+Hidden +DoubleTime".
    tail = text.split("]", 1)[-1]
    acronyms = []
    for name in re.findall(r"\+(\w+)", tail):
        if name in NP_MOD_NAMES:
            acronyms.append(NP_MOD_NAMES[name])
    return "".join(acronyms) or None
