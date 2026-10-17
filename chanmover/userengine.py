"""The Premium-account engine (Telethon, MTProto).

Bots cannot put Premium (custom) emoji into *channel* posts, whoever owns them; a Premium user can. This backend acts as
that user: it reads the source posts, upgrades ordinary emoji to Premium emoji, and writes them to the destination
without any "Forwarded from" header. Media is reused by reference (nothing is downloaded or uploaded). Posts that need no
change are forwarded server-side without the author, 100 per call.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from telethon import TelegramClient, errors
from telethon.tl import functions, types

from . import __version__
from .emoji import EmojiMap

log = logging.getLogger("chanmover.user")

FATAL = (errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError, errors.ChannelPrivateError,
         errors.UserBannedInChannelError, errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
         errors.UserDeactivatedError, errors.ChannelInvalidError, errors.PeerIdInvalidError)
# media kinds that cannot be re-sent from a message's media reference: they are forwarded as they are
FORWARD_ONLY = (types.MessageMediaPoll, types.MessageMediaGeo, types.MessageMediaGeoLive, types.MessageMediaVenue,
                types.MessageMediaContact, types.MessageMediaDice, types.MessageMediaGame, types.MessageMediaInvoice,
                types.MessageMediaUnsupported, types.MessageMediaStory, types.MessageMediaGiveaway,
                types.MessageMediaGiveawayResults)


class Pacer:
    """Keeps the account's writing to a gentle, steady pace and backs off after every flood wait.

    Telegram does not publish its limits; hammering them makes the waits grow. So: `base` seconds per post written
    (with a little jitter), doubled after a flood wait (x4 after a long one, up to x8) and eased back 3 % per call."""

    def __init__(self, base: float):
        self.base, self.factor, self.next_at = base, 1.0, 0.0

    async def before(self) -> None:
        delay = self.next_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def after(self, posts: int) -> None:
        self.next_at = time.monotonic() + self.base * self.factor * max(posts, 1) * (1 + random.uniform(0, 0.3))
        self.factor = max(1.0, self.factor * 0.97)

    def hold_until(self, epoch: float, margin: float = 60.0) -> None:
        """Stay quiet until a wall-clock time (a flood wait that was still running when the service restarted)."""
        self.next_at = max(self.next_at, time.monotonic() + max(0.0, epoch - time.time()) + margin)

    def flooded(self, seconds: float) -> None:
        self.factor = min(8.0, self.factor * (4 if seconds >= 300 else 2))


@dataclass
class Result:
    pairs: list[tuple[int, int]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    skipped: int = 0
    upgraded: int = 0     # posts rewritten with Premium emoji
    buttons_lost: int = 0  # posts whose URL buttons could not be re-attached


class Hooks:
    """What the engine plugs in: flood-wait handling and copying a post together with its URL buttons (bot only)."""
    async def flood(self, seconds: float) -> None:
        await asyncio.sleep(seconds + 1)

    async def with_buttons(self, src_id: int, rows: list) -> int | None:
        return None


def url_rows(msg) -> list[list[list[str]]]:
    """URL-button rows of a message as [[text, url], ...] (only URL buttons survive outside the original bot).

    Uses Telethon's `message.buttons`, which hides how each TL layer spells the button types."""
    rows = []
    for row in getattr(msg, "buttons", None) or []:
        cells = [[b.text, b.url] for b in row if getattr(b, "url", None)]
        if cells:
            rows.append(cells)
    return rows


def _entity_kind(e) -> str:
    name = type(e).__name__.replace("MessageEntity", "")
    return {"CustomEmoji": "custom_emoji", "Code": "code", "Pre": "pre"}.get(name, name.lower())


class UserBackend:
    def __init__(self, api_id: int, api_hash: str, session: str, emoji: EmojiMap, interval: float = 0.8):
        # flood_sleep_threshold=0: surface every FloodWait so the engine can show it and slow down
        self.client = TelegramClient(session, api_id, api_hash, device_model="Channel Mover", system_version="Linux",
                                     app_version=__version__, flood_sleep_threshold=0)
        self.emoji = emoji
        self.pacer = Pacer(interval)
        self._peers: dict[int, object] = {}

    # session --------------------------------------------------------------------------------------------------
    async def connect(self) -> bool:
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    async def whoami(self) -> dict | None:
        """The linked account (cached for a minute: menus call this often and each call is an API request)."""
        cached = getattr(self, "_me", None)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        if not await self.connect():
            self._me = None
            return None
        me = await self.client.get_me()
        info = {"id": me.id, "name": " ".join(x for x in (me.first_name, me.last_name) if x) or str(me.id),
                "username": me.username, "premium": bool(getattr(me, "premium", False))}
        self._me = (time.monotonic(), info)
        return info

    async def qr_login(self):
        await self.connect()
        return await self.client.qr_login()

    async def finish_login(self) -> None:
        await self.client.get_dialogs(limit=200)  # fills the access-hash cache for the channels

    async def logout(self) -> None:
        self._me = None
        if await self.connect():
            await self.client.log_out()
        else:
            await self.client.disconnect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def peer(self, chat_id: int):
        if chat_id not in self._peers:
            try:
                self._peers[chat_id] = await self.client.get_input_entity(chat_id)
            except ValueError:
                await self.client.get_dialogs(limit=500)
                self._peers[chat_id] = await self.client.get_input_entity(chat_id)
        return self._peers[chat_id]

    async def check_access(self, src_id: int, dst_id: int) -> str | None:
        """None when the account can read the source and post to the destination, else what is missing."""
        try:
            await self.peer(src_id)
        except Exception:
            return "اکانت پریمیوم عضو کانال مبدا نیست."
        try:
            await self.peer(dst_id)
            perms = await self.client.get_permissions(dst_id, "me")
        except Exception:
            return "اکانت پریمیوم عضو کانال مقصد نیست."
        if not (perms.is_creator or (perms.is_admin and perms.post_messages)):
            return "اکانت پریمیوم در کانال مقصد ادمین با دسترسی «ارسال پیام» نیست."
        return None

    # reading --------------------------------------------------------------------------------------------------
    async def scan(self, src_id: int, lo: int = 1, hi: int | None = None,
                   progress: Callable[[int, int], Awaitable[None]] | None = None,
                   on_flood: Callable[[float], Awaitable[None]] | None = None) -> tuple[list[int], dict[int, tuple[int, int]]]:
        """Ids of every real post of the source (ascending) and the id span of each multi-post album.

        A flood wait in the middle of the history is waited out and the listing continues after the last id seen."""
        src = await self.peer(src_id)
        ids: list[int] = []
        groups: dict[int, list[int]] = {}
        last = max(lo - 1, 0)
        while True:
            try:
                async for m in self.client.iter_messages(src, reverse=True, min_id=last, max_id=(hi + 1) if hi else 0,
                                                         wait_time=1.5):
                    last = m.id
                    if isinstance(m, (types.MessageService, types.MessageEmpty)):
                        continue
                    ids.append(m.id)
                    if m.grouped_id:
                        groups.setdefault(m.grouped_id, []).append(m.id)
                    if progress and len(ids) % 200 == 0:
                        await progress(len(ids), m.id)
                break
            except errors.FloodWaitError as e:
                log.warning("flood wait %ss while listing the source", e.seconds)
                await (on_flood or Hooks().flood)(e.seconds)
        return ids, {i: (g[0], g[-1]) for g in groups.values() if len(g) > 1 for i in g}

    # copying --------------------------------------------------------------------------------------------------
    async def _call(self, hooks: Hooks, make: Callable[[], Awaitable]):
        tries = 0
        while True:
            try:
                return await make()
            except errors.FloodWaitError as e:
                self.pacer.flooded(e.seconds)
                log.warning("%s: flood wait %ss (pace is now %.1fs per post)", getattr(e.request, "__class__", type(e)).__name__,
                            e.seconds, self.pacer.base * self.pacer.factor)
                await hooks.flood(e.seconds)
            except (ConnectionError, asyncio.TimeoutError) as e:
                tries += 1
                if tries > 4:
                    raise
                log.warning("network trouble (%s), retrying", e)
                await asyncio.sleep(5 * tries)

    @staticmethod
    def _new_ids(updates, random_ids: list[int]) -> list[int | None]:
        by_random = {u.random_id: u.id for u in getattr(updates, "updates", []) if isinstance(u, types.UpdateMessageID)}
        return [by_random.get(r) for r in random_ids]

    def _entities(self, text: str, entities, upgrade: bool, emoji: EmojiMap):
        """(text, entities) for the copy: the post's own entities plus Premium emoji for the ordinary ones."""
        own = list(entities or [])
        if not (upgrade and text):
            return text, own, 0
        spans = [(e.offset, e.length, _entity_kind(e)) for e in own]
        new_text, added = emoji.upgrade(text, spans)
        if not added:
            return text, own, 0
        own += [types.MessageEntityCustomEmoji(offset=o, length=n, document_id=int(cid)) for o, n, cid in added]
        own.sort(key=lambda e: e.offset)
        return new_text, own, len(added)

    async def copy_window(self, src_id: int, dst_id: int, ids: list[int], *, silent: bool = True, protect: bool = False,
                          no_caption: bool = False, upgrade: bool = True, emoji: EmojiMap | None = None,
                          hooks: Hooks | None = None) -> Result:
        res = Result()
        try:
            return await self._copy_window(res, src_id, dst_id, ids, silent, protect, no_caption, upgrade,
                                           emoji or self.emoji, hooks or Hooks())
        except BaseException as e:
            e.partial = res  # what was already sent before the error, so the engine can record it
            raise

    async def _copy_window(self, res: Result, src_id: int, dst_id: int, ids: list[int], silent: bool, protect: bool,
                           no_caption: bool, upgrade: bool, emoji: EmojiMap, hooks: Hooks) -> Result:
        src, dst = await self.peer(src_id), await self.peer(dst_id)
        msgs = await self._call(hooks, lambda: self.client.get_messages(src, ids=ids))
        pending: list = []  # consecutive posts that can be forwarded as they are

        async def flush():
            if not pending:
                return
            batch = list(pending)
            pending.clear()
            rids = [random.getrandbits(62) for _ in batch]
            await self.pacer.before()
            try:
                r = await self._call(hooks, lambda: self.client(functions.messages.ForwardMessagesRequest(
                    from_peer=src, id=[m.id for m in batch], random_id=rids, to_peer=dst, silent=silent or None,
                    drop_author=True, drop_media_captions=no_caption or None, noforwards=protect or None)))
            except FATAL:
                raise
            except errors.RPCError as e:
                res.failed += [(m.id, type(e).__name__) for m in batch]
                return
            finally:
                self.pacer.after(len(batch))
            for m, new in zip(batch, self._new_ids(r, rids)):
                if new:
                    res.pairs.append((m.id, new))
                else:
                    res.skipped += 1

        async def send(group: list):
            """Re-send one post (or album) with upgraded entities, reusing its media."""
            texts, ents, count = [], [], 0
            for m in group:
                text = "" if (no_caption and m.media) else (m.raw_text or "")
                t, e, n = self._entities(text, m.entities, upgrade, emoji)
                texts.append(t); ents.append(e); count += n
            first = group[0]
            await self.pacer.before()
            try:
                if len(group) > 1:
                    sent = await self._call(hooks, lambda: self.client.send_file(
                        dst, [m.media for m in group], caption=texts, formatting_entities=ents, silent=silent))
                elif first.media and not isinstance(first.media, types.MessageMediaWebPage):
                    sent = [await self._call(hooks, lambda: self.client.send_file(
                        dst, first.media, caption=texts[0], formatting_entities=ents[0], silent=silent))]
                else:
                    sent = [await self._call(hooks, lambda: self.client.send_message(
                        dst, texts[0], formatting_entities=ents[0], silent=silent,
                        link_preview=isinstance(first.media, types.MessageMediaWebPage)))]
            except FATAL:
                raise
            except errors.RPCError as e:
                res.failed += [(m.id, type(e).__name__) for m in group]
                return
            finally:
                self.pacer.after(len(group))
            sent = sent if isinstance(sent, list) else [sent]
            for m, new in zip(group, sent):
                res.pairs.append((m.id, new.id))
            res.upgraded += 1 if count else 0

        i = 0
        order = list(zip(ids, msgs))
        while i < len(order):
            mid, m = order[i]
            i += 1
            if m is None or isinstance(m, (types.MessageEmpty, types.MessageService)):
                res.skipped += 1
                continue
            rows = url_rows(m)
            if rows:
                await flush()
                new = await hooks.with_buttons(m.id, rows)
                if new:
                    res.pairs.append((m.id, new))
                    continue
                res.buttons_lost += 1  # no bot to carry the buttons: the post itself still goes through below
            group = [m]
            if m.grouped_id:  # an album stays together
                while i < len(order) and order[i][1] is not None and getattr(order[i][1], "grouped_id", None) == m.grouped_id:
                    group.append(order[i][1])
                    i += 1
            rewrite = False
            if upgrade and not isinstance(m.media, FORWARD_ONLY):
                for g in group:
                    spans = [(e.offset, e.length, _entity_kind(e)) for e in (g.entities or [])]
                    if g.raw_text and emoji.upgrade(g.raw_text, spans)[1]:
                        rewrite = True
                        break
            if rewrite:
                await flush()
                await send(group)
            else:
                pending.extend(group)
        await flush()
        return res
