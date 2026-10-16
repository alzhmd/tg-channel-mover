"""Runtime configuration, read from the environment (and an optional .env file in the working directory)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit())


@dataclass(frozen=True)
class Config:
    token: str
    admin_ids: frozenset[int]
    claim_code: str
    db_path: str
    batch_size: int      # max messages per copyMessages call (Telegram allows 100)
    batch_delay: float   # pause between two calls, seconds
    lookahead: int       # private sources: stop after this many empty ids past the last post found
    scrape_delay: float  # pause between two t.me/s pages, seconds
    api_id: int          # Telegram app credentials, only for the Premium-account engine (my.telegram.org)
    api_hash: str
    user_session: str    # Telethon session file of the connected Premium account
    emoji_file: str      # ordinary -> Premium emoji map (empty: the bundled one)
    user_batch: int      # posts per window with the Premium-account engine

    @classmethod
    def load(cls) -> "Config":
        _load_env_file(Path(os.environ.get("ENV_FILE", ".env")))
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("BOT_TOKEN is not set (see .env.example)")
        return cls(
            token=token,
            admin_ids=_ids(os.environ.get("ADMIN_IDS", "")),
            claim_code=os.environ.get("CLAIM_CODE", "").strip(),
            db_path=os.environ.get("DB_PATH", "data/chanmover.db"),
            batch_size=max(1, min(100, int(os.environ.get("BATCH_SIZE", "20")))),
            batch_delay=float(os.environ.get("BATCH_DELAY", "1.0")),
            lookahead=int(os.environ.get("LOOKAHEAD", "600")),
            scrape_delay=float(os.environ.get("SCRAPE_DELAY", "0.35")),
            api_id=int(os.environ.get("TG_API_ID") or 0),
            api_hash=os.environ.get("TG_API_HASH", "").strip(),
            user_session=os.environ.get("USER_SESSION", "data/user"),
            emoji_file=os.environ.get("PREMIUM_EMOJI_FILE", "").strip(),
            user_batch=max(1, min(100, int(os.environ.get("USER_BATCH_SIZE", "40")))),
        )
