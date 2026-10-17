"""Entry point: python -m chanmover"""
from __future__ import annotations

import logging

from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

from .config import Config
from .db import Database
from .engine import Engine
from .handlers import COMMANDS, UiNotifier, register


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = Config.load()

    async def post_init(app: Application) -> None:
        db = await Database.open(cfg.db_path)
        notifier = UiNotifier(app)
        user = None
        if cfg.api_id and cfg.api_hash:
            try:  # the Premium-account engine is optional and needs telethon + qrcode
                from .emoji import EmojiMap
                from .userengine import UserBackend
                user = UserBackend(cfg.api_id, cfg.api_hash, cfg.user_session, EmojiMap.load(cfg.emoji_file or None),
                                   cfg.user_interval)
                await user.connect()
                # a restart never skips a flood wait that was still running, and always starts quietly
                user.pacer.hold_until(float(await db.get_kv("user_flood_until", "0") or 0), margin=60.0)
            except ImportError:
                logging.getLogger("chanmover").warning("telethon is not installed: Premium-account engine disabled")
        engine = Engine(app.bot, db, cfg, notifier, user=user)
        notifier.engine = engine
        app.bot_data.update(db=db, engine=engine, cfg=cfg, user=user)
        await app.bot.set_my_commands(COMMANDS)
        await engine.resume_all()
        me = await app.bot.get_me()
        logging.getLogger("chanmover").info("started as @%s", me.username)

    async def post_stop(app: Application) -> None:
        # PTB closes the bot's HTTP client right after this hook, so the window in flight has to finish here
        await app.bot_data["engine"].shutdown()

    async def post_shutdown(app: Application) -> None:
        if app.bot_data.get("user"):
            await app.bot_data["user"].disconnect()
        await app.bot_data["db"].close()

    app = (Application.builder().token(cfg.token)
           .connect_timeout(15).read_timeout(30).write_timeout(30).pool_timeout(15)
           .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
           .post_init(post_init).post_stop(post_stop).post_shutdown(post_shutdown).build())
    register(app)
    app.run_polling(allowed_updates=["message", "callback_query", "my_chat_member", "channel_post"])


if __name__ == "__main__":
    main()
