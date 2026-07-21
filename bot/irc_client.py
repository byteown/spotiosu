"""Minimal asyncio IRC client for osu!'s Bancho chat server.

Bancho speaks a small subset of IRC. We only need to:
  * authenticate with PASS/NICK/USER,
  * answer PING keepalives,
  * receive private messages (PRIVMSG) and /np ACTIONs,
  * send private messages back (throttled, since Bancho rate-limits).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger("spotiosu.irc")


@dataclass
class IncomingMessage:
    sender: str          # osu! username of the person who messaged the bot
    text: str            # message body
    is_action: bool      # True for /np and other CTCP ACTIONs


MessageHandler = Callable[[IncomingMessage, "IrcClient"], Awaitable[None]]


class IrcClient:
    def __init__(
        self,
        username: str,
        password: str,
        server: str = "irc.ppy.sh",
        port: int = 6667,
        handler: MessageHandler | None = None,
    ) -> None:
        self.username = username
        self._password = password
        self._server = server
        self._port = port
        self._handler = handler
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = asyncio.Lock()
        self._last_send = 0.0
        # Bancho allows ~1 msg/sec sustained for normal users; be conservative.
        self._min_send_interval = 1.2
        self._running = False

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def connect(self) -> None:
        log.info("Connecting to %s:%s", self._server, self._port)
        self._reader, self._writer = await asyncio.open_connection(self._server, self._port)
        await self._raw(f"PASS {self._password}")
        await self._raw(f"NICK {self.username}")
        # USER is ignored by Bancho but keeps us compliant with the protocol.
        await self._raw(f"USER {self.username} 0 * :{self.username}")

    async def _raw(self, line: str) -> None:
        assert self._writer is not None
        self._writer.write((line + "\r\n").encode("utf-8", errors="replace"))
        await self._writer.drain()

    async def send_message(self, target: str, text: str) -> None:
        """Send a PRIVMSG, throttled and split into <=400-char chunks."""
        for chunk in _split_message(text):
            async with self._send_lock:
                wait = self._min_send_interval - (time.monotonic() - self._last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
                log.info(">> PRIVMSG %s: %s", target, chunk[:80])
                await self._raw(f"PRIVMSG {target} :{chunk}")
                self._last_send = time.monotonic()

    async def run(self) -> None:
        """Main receive loop. Reconnects on connection loss."""
        self._running = True
        while self._running:
            try:
                if self._reader is None:
                    await self.connect()
                await self._read_loop()
            except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
                log.warning("Connection lost (%s); reconnecting in 10s", exc)
                await self._cleanup()
                await asyncio.sleep(10)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while self._running:
            raw = await self._reader.readline()
            if not raw:
                raise ConnectionError("server closed the connection")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            await self._dispatch(line)

    async def _dispatch(self, line: str) -> None:
        log.debug("<< %s", line)
        if line.startswith("PING"):
            await self._raw("PONG" + line[4:])
            return

        prefix, command, params = _parse_line(line)
        if command == "PRIVMSG":
            await self._on_privmsg(prefix, params)
        elif command in ("464",):  # ERR_PASSWDMISMATCH
            log.error("Authentication failed - check your IRC username/password.")
            self._running = False
        elif command in ("401", "404"):  # ERR_NOSUCHNICK / ERR_CANNOTSENDTOCHAN
            target = params[1] if len(params) > 1 else "?"
            log.warning(
                "osu! could not deliver your reply to '%s' (No such nick). "
                "They may be offline, on osu!lazer, or the username differs.",
                target,
            )
        elif command == "001":  # RPL_WELCOME
            log.info("Authenticated with Bancho as %s", self.username)

    async def _on_privmsg(self, prefix: str, params: list[str]) -> None:
        if not params or self._handler is None:
            return
        sender = prefix.split("!", 1)[0]
        text = params[-1]
        is_action = False
        if text.startswith("\x01ACTION") and text.endswith("\x01"):
            is_action = True
            text = text[len("\x01ACTION"): -1].strip()
        # Ignore messages the bot itself might echo, and channel messages.
        if sender.lower() == self.username.lower():
            return
        msg = IncomingMessage(sender=sender, text=text, is_action=is_action)
        try:
            await self._handler(msg, self)
        except Exception:  # noqa: BLE001 - never let one message kill the loop
            log.exception("Handler raised on message from %s", sender)

    async def _cleanup(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None

    async def stop(self) -> None:
        self._running = False
        await self._cleanup()


def _parse_line(line: str) -> tuple[str, str, list[str]]:
    """Parse an IRC line into (prefix, command, params)."""
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    # The trailing param starts at " :".
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    parts = line.split()
    command = parts[0] if parts else ""
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return prefix, command, params


def _split_message(text: str, limit: int = 400) -> list[str]:
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
