"""Entry point: wire everything together and run the bot."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .commands import BotContext, handle_message
from .config import Config, ConfigError
from .irc_client import IncomingMessage, IrcClient
from .osu_api import OsuApi
from .pp import PpCalculator
from .recommender import Recommender
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down httpx request logging unless verbose.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def _build_context(config: Config) -> tuple[OsuApi, BotContext]:
    api = OsuApi(config.api.client_id, config.api.client_secret)
    pp = PpCalculator()
    store = Store()
    reco = Recommender(
        api, pp, store,
        star_window=config.bot.star_window,
        candidate_pages=config.bot.candidate_pages,
    )
    ctx = BotContext(config, api, reco, store)
    if not pp.available:
        logging.getLogger("spotiosu").warning(
            "rosu-pp-py not installed — recommendations will omit pp values. "
            "Run: pip install rosu-pp-py"
        )
    return api, ctx


async def _api_sanity_check(api: OsuApi, config: Config) -> None:
    log = logging.getLogger("spotiosu")
    try:
        me = await api.get_user(config.irc.username, mode=config.bot.default_mode)
        if me:
            log.info("osu! API OK (account resolves to user id %s)", me.get("id"))
    except Exception as exc:  # noqa: BLE001
        log.error("osu! API check failed: %s", exc)
        await api.close()
        raise


class _ConsolePeer:
    """Duck-types the IrcClient interface so command handlers can reply to stdout."""

    def __init__(self, username: str) -> None:
        self.username = username

    async def send_message(self, target: str, text: str) -> None:  # noqa: ARG002
        print(text)


async def _run_console(config: Config) -> None:
    """Interactive local mode: type commands, get recommendations for yourself.

    Sidesteps osu!'s no-self-messaging rule so you can use the bot solo without a
    second account. Commands behave exactly like the chat bot's.
    """
    log = logging.getLogger("spotiosu")
    api, ctx = _build_context(config)
    await _api_sanity_check(api, config)
    peer = _ConsolePeer(config.irc.username)

    print(f"\nspotiosu console — recommendations for '{config.irc.username}'.")
    print("Commands: !r [mods] · !with <mods> · !like · !dislike · !reset · !help · quit\n")
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                line = await loop.run_in_executor(None, input, "> ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if line in ("quit", "exit", ":q"):
                break
            if not line:
                continue
            # Bare beatmap URL -> treat like /np.
            is_action = "osu.ppy.sh" in line and line.startswith(("http", "["))
            msg = IncomingMessage(sender=config.irc.username, text=line, is_action=is_action)
            await handle_message(msg, peer, ctx)  # type: ignore[arg-type]
    finally:
        await api.close()
        log.info("Console closed.")


async def _run(config: Config) -> None:
    log = logging.getLogger("spotiosu")
    api, ctx = _build_context(config)
    await _api_sanity_check(api, config)

    irc = IrcClient(
        username=config.irc.username,
        password=config.irc.password,
        server=config.irc.server,
        port=config.irc.port,
    )

    async def on_message(msg: IncomingMessage, client: IrcClient) -> None:
        await handle_message(msg, client, ctx)

    irc.set_handler(on_message)

    log.info("spotiosu is starting. PM '%s' in osu! and send !help",
             config.irc.username)
    try:
        await irc.run()
    finally:
        await irc.stop()
        await api.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spotiosu", description="Tillerino-style osu! bot")
    parser.add_argument("-c", "--config", default="config.json", help="path to config.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--console", action="store_true",
                        help="run locally in the terminal (no IRC) — use the bot solo")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("spotiosu")

    try:
        config = Config.load(Path(args.config))
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    runner = _run_console(config) if args.console else _run(config)
    try:
        asyncio.run(runner)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
