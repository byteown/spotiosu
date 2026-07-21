"""Configuration loading and validation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


ENV_PREFIX = "SPOTIOSU_"


def _env(name: str) -> str | None:
    """Read SPOTIOSU_<NAME> from the environment, treating blanks as unset."""
    value = os.environ.get(ENV_PREFIX + name, "").strip()
    return value or None


class ConfigError(Exception):
    """Raised when config.json is missing or incomplete."""


@dataclass
class IrcConfig:
    username: str
    password: str
    server: str = "irc.ppy.sh"
    port: int = 6667


@dataclass
class ApiConfig:
    client_id: int
    client_secret: str


@dataclass
class BotConfig:
    command_prefix: str = "!"
    default_mode: str = "osu"
    star_window: float = 0.7
    recent_seen_limit: int = 400
    candidate_pages: int = 2


@dataclass
class DatabaseConfig:
    dsn: str = "postgresql://spotiosu:spotiosu@localhost:5432/spotiosu"


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    # Public base URL the browser uses; must match the osu! OAuth callback origin.
    public_base: str = "http://localhost:8000"
    # Set in production so sessions survive restarts; otherwise a local file is used.
    session_secret: str = ""

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_base.rstrip('/')}/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        """Mark the session cookie Secure whenever we are served over HTTPS."""
        return self.public_base.lower().startswith("https://")


@dataclass
class Config:
    irc: IrcConfig
    api: ApiConfig
    bot: BotConfig = field(default_factory=BotConfig)
    web: WebConfig = field(default_factory=WebConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Load configuration from config.json and/or environment variables.

        Locally you keep everything in config.json. In production (Docker) there
        is no config file at all - every value comes from the environment, so no
        secret ever has to be committed. Environment always wins over the file.
        """
        path = Path(path)
        raw: dict = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        elif not os.environ.get("SPOTIOSU_CLIENT_ID"):
            raise ConfigError(
                f"Config file not found: {path}\n"
                f"Copy config.example.json to {path.name} and fill in your credentials,\n"
                f"or set SPOTIOSU_CLIENT_ID / SPOTIOSU_CLIENT_SECRET in the environment."
            )

        api_raw = raw.get("osu_api", {})
        irc_raw = raw.get("osu_irc", {})  # optional: only for the IRC/console bot
        bot_raw = raw.get("bot", {})
        web_raw = raw.get("web", {})
        db_raw = raw.get("database", {})

        irc = IrcConfig(
            username=str(irc_raw.get("username", "")).strip(),
            password=str(irc_raw.get("password", "")).strip(),
            server=irc_raw.get("server", "irc.ppy.sh"),
            port=int(irc_raw.get("port", 6667)),
        )
        api = ApiConfig(
            client_id=int(_env("CLIENT_ID") or api_raw.get("client_id") or 0),
            client_secret=str(
                _env("CLIENT_SECRET") or api_raw.get("client_secret") or ""
            ).strip(),
        )
        bot = BotConfig(
            command_prefix=bot_raw.get("command_prefix", "!"),
            default_mode=bot_raw.get("default_mode", "osu"),
            star_window=float(bot_raw.get("star_window", 0.7)),
            recent_seen_limit=int(bot_raw.get("recent_seen_limit", 400)),
            candidate_pages=int(bot_raw.get("candidate_pages", 2)),
        )
        web = WebConfig(
            host=_env("HOST") or web_raw.get("host", "127.0.0.1"),
            port=int(_env("PORT") or web_raw.get("port", 8000)),
            public_base=_env("PUBLIC_BASE")
            or web_raw.get("public_base", "http://localhost:8000"),
            session_secret=_env("SESSION_SECRET") or "",
        )
        database = DatabaseConfig(
            dsn=_env("DSN") or db_raw.get("dsn", DatabaseConfig.dsn),
        )

        _validate_api(api)
        return cls(irc=irc, api=api, bot=bot, web=web, database=database)


def _validate_api(api: ApiConfig) -> None:
    problems = []
    if not api.client_id:
        problems.append("osu_api.client_id is not set")
    if not api.client_secret or api.client_secret.startswith("your-oauth"):
        problems.append("osu_api.client_secret is not set")
    if problems:
        raise ConfigError("Config incomplete:\n  - " + "\n  - ".join(problems))


def validate_irc(irc: IrcConfig) -> None:
    """Validate IRC credentials; only required for the IRC/console bot modes."""
    problems = []
    if not irc.username or irc.username == "YourOsuUsername":
        problems.append("osu_irc.username is not set")
    if not irc.password or irc.password.startswith("your-irc-password"):
        problems.append("osu_irc.password is not set (get it at https://osu.ppy.sh/p/irc)")
    if problems:
        raise ConfigError("IRC config incomplete:\n  - " + "\n  - ".join(problems))
