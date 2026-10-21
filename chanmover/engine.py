"""The copy engine.

A job first builds a *plan* (the exact ids of a public source from its web preview, or an id range for a private
one), then copies it oldest-first in windows with Bot API `copyMessages` — server-side, no download, no forward
header, albums kept together. Flood limits, restarts, pause/cancel and unsendable messages are all handled here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from . import scrape
from .config import Config
from .db import Database
from .emoji import load_map

log = logging.getLogger("chanmover.engine")

try:  # the Premium-account engine is optional (needs telethon)
    from .userengine import FATAL as USER_FATAL
    from .userengine import Hooks as UserHooks
except ImportError:  # pragma: no cover
    USER_FATAL, UserHooks = (), object

# Error texts as returned by the real Bot API (checked against copyMessage / copyMessages)
FATAL_HINTS = ("not enough rights", "chat not found", "no rights", "not a member", "kicked", "write_forbidden",
               "need administrator", "channel_private", "bot was", "chat_admin_required", "have no rights",
               "message to forward not found")  # the latter: the bot can no longer read the source chat
EMPTY_HINTS = ("message to copy not found", "messages not found", "message_id_invalid", "no messages to forward",
               "message not found")
UNSUPPORTED_HINTS = ("can't be copied", "can't be forwarded")  # service messages and types Telegram won't copy
SEND_TIMEOUTS = dict(read_timeout=120, write_timeout=60, connect_timeout=20, pool_timeout=20)


class FatalError(Exception):
    """Something only a human can fix (bot kicked, missing rights, ...)."""


class Stopped(Exception):
    """The job was cancelled or the service is shutting down; the current window was not sent."""


def classify(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, Forbidden) or any(h in text for h in FATAL_HINTS):
        return "fatal"
    if any(h in text for h in EMPTY_HINTS):
        return "empty"
    if any(h in text for h in UNSUPPORTED_HINTS):
        return "unsupported"
    return "other"


def seconds(value) -> float:
    return value.total_seconds() if hasattr(value, "total_seconds") else float(value)


def markup_of(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url) for text, url in row] for row in rows])


@dataclass
class Batch:
    pairs: list[tuple[int, int]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    windows: list[tuple[int, int, list[int]]] = field(default_factory=list)  # copied, but old->new not 1:1
    copied: int = 0
    skipped: int = 0
    upgraded: int = 0      # Premium-account engine: posts rewritten with Premium emoji
    buttons_lost: int = 0

    def merge(self, other: Batch) -> Batch:
        self.pairs += other.pairs
        self.failed += other.failed
        self.windows += other.windows
        self.copied += other.copied
        self.skipped += other.skipped
        self.upgraded += other.upgraded
        self.buttons_lost += other.buttons_lost
        return self


class ListPlan:
    """Exact list of source ids (retries, and the Premium-account engine, which reads the real list of posts)."""
    mode = "list"

    def __init__(self, ids: list[int], pos: int = 0, albums: dict[int, tuple[int, int]] | None = None):
        self.ids, self.pos, self.albums = ids, pos, albums or {}

    @property
    def total(self) -> int:
        return len(self.ids)

    def next(self, n: int) -> list[int] | None:
        window = self.ids[self.pos:self.pos + n]
        while window and window[-1] in self.albums and window[-1] < self.albums[window[-1]][1] \
                and self.pos + len(window) < len(self.ids):
            window.append(self.ids[self.pos + len(window)])  # an album is never split between two windows
        return window or None

    def advance(self, window: list[int], hits: int) -> None:
        self.pos += len(window)

    @property
    def remaining(self) -> int | None:
        return self.total - self.pos

    @property
    def top(self) -> int:
        return self.ids[-1] if self.ids else 0


class RangePlan:
    """Copy every id from `start` on, until `lookahead` ids past the newest post found.

    Contiguous ranges (not a list of known ids) so nothing is missed: the web preview of a public channel shows an
    album as one post, and a private channel has no preview at all."""
    mode = "range"

    def __init__(self, start: int, hint_top: int, lookahead: int, pos: int | None = None, last_hit: int = 0,
                 end: int | None = None, known: set[int] | None = None):
        self.start, self.hint_top, self.lookahead, self.end = start, hint_top, lookahead, end
        self.pos = pos or start
        self.last_hit = last_hit
        self.known = known or set()
        self.max_known = max(self.known) if self.known else 0

    @property
    def total(self) -> int:
        return self.hint_top

    @property
    def _limit(self) -> int:
        limit = max(self.hint_top, self.last_hit) + self.lookahead
        return min(limit, self.end) if self.end else limit

    def next(self, n: int) -> list[int] | None:
        limit = self._limit
        if self.pos > limit:
            return None
        window: list[int] = []
        i = self.pos
        if self.known and i <= self.max_known:
            limit = min(limit, self.max_known)  # the tail past the newest listed post is scanned in its own windows
        while i <= limit and len(window) < n:
            window.append(i)
            i += 1
            if self.known and i <= self.max_known and i not in self.known:
                # ids the preview did not list (album members, or deleted posts) end the window, so that at most one
                # such gap sits in it and the old->new pairing stays exact whenever the gap is all-or-nothing
                while i <= min(limit, self.max_known) and i not in self.known and len(window) < 100:
                    window.append(i)
                    i += 1
                break
        return window

    def advance(self, window: list[int], hits: int) -> None:
        self.pos = window[-1] + 1
        if hits:
            self.last_hit = max(self.last_hit, window[-1])

    @property
    def remaining(self) -> int | None:
        return max(0, self.hint_top - self.pos) if self.hint_top else None

    @property
    def top(self) -> int:
        return self.last_hit


@dataclass
class RunState:
    task: asyncio.Task | None = None
    running: asyncio.Event = field(default_factory=asyncio.Event)
    cancel: bool = False
    stop: bool = False  # service shutdown: finish the window in flight, keep the job 'running' for the next start
    bs: int = 20
    samples: deque = field(default_factory=lambda: deque(maxlen=40))
    processed: int = 0
    last_tick: float = 0.0
    flood_until: float = 0.0
    retry: list[int] | None = None
    known: set[int] | None = None  # ids the web preview showed; lets old->new pairs stay exact around gaps
    kbs: dict[int, list] | None = None  # post id -> URL-button rows, copied one by one with their buttons
    trailing: int = 0  # empty ids seen since the last post; only counted as skipped once a later post shows up
    predicted: dict[int, int] | None = None  # Premium engine: source id -> the id the copy will get in the destination
    rules: tuple | None = None  # link rules (+ target) used while copying


class Notifier:
    """Callbacks the UI layer plugs in; the engine never imports the UI."""
    async def progress(self, job_id: int) -> None: ...
    async def finished(self, job_id: int) -> None: ...
    async def failed(self, job_id: int, reason: str) -> None: ...
    async def notice(self, job_id: int, text: str) -> None: ...


class Engine:
    def __init__(self, bot: Bot, db: Database, cfg: Config, notifier: Notifier | None = None, user=None):
        self.bot, self.db, self.cfg, self.user = bot, db, cfg, user
        self.fixer = None  # LinkFixer, set by the entry point when the Premium account is available
        self.notifier = notifier or Notifier()
        self.state: dict[int, RunState] = {}
        self._drain_locks: dict[int, asyncio.Lock] = {}

    # control ------------------------------------------------------------------------------------------------
    def is_active(self, job_id: int) -> bool:
        st = self.state.get(job_id)
        return bool(st and st.task and not st.task.done())

    async def start(self, job_id: int, retry: list[int] | None = None) -> None:
        if self.is_active(job_id):
            return
        st = RunState(bs=self.cfg.batch_size, retry=retry)
        st.running.set()
        self.state[job_id] = st
        st.task = asyncio.create_task(self._run(job_id, st), name=f"job-{job_id}")

    async def pause(self, job_id: int) -> None:
        st = self.state.get(job_id)
        if st and self.is_active(job_id):
            st.running.clear()
            await self.db.update_job(job_id, status="paused")
            await self._tick(job_id, st, force=True)

    async def resume(self, job_id: int) -> None:
        if self.is_active(job_id):
            st = self.state[job_id]
            await self.db.update_job(job_id, status="running")
            st.running.set()
        else:
            await self.start(job_id)

    async def cancel(self, job_id: int) -> None:
        st = self.state.get(job_id)
        if st and self.is_active(job_id):
            st.cancel = True
            st.running.set()
        else:
            await self.db.update_job(job_id, status="cancelled", finished_at=time.time())

    async def resume_all(self) -> None:
        for job in await self.db.jobs_with_status("scanning", "running"):
            await self.start(job["id"])
        for job in await self.db.jobs_with_status("done"):
            if await self.db.live_queue(job["id"]):
                asyncio.create_task(self.drain_live(job["id"], force=True))

    async def shutdown(self) -> None:
        """Let every running job finish the window it is copying (and commit it) before the process exits."""
        tasks = [st.task for st in self.state.values() if st.task and not st.task.done()]
        for st in self.state.values():
            st.stop = True
            st.running.set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=60)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # stats --------------------------------------------------------------------------------------------------
    def stats(self, job_id: int) -> dict:
        """Recent speed (ids processed per minute) and ETA seconds; empty when unknown."""
        st = self.state.get(job_id)
        if not st or len(st.samples) < 2:
            return {}
        t0, n0 = st.samples[0]
        t1, n1 = st.samples[-1]
        if t1 <= t0 or n1 <= n0:
            return {}
        per_min = (n1 - n0) / (t1 - t0) * 60
        return {"per_min": per_min, "flood_until": st.flood_until}

    # the job ------------------------------------------------------------------------------------------------
    async def _tick(self, job_id: int, st: RunState, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - st.last_tick < 5:
            return
        st.last_tick = now
        try:
            await self.notifier.progress(job_id)
        except Exception:  # a failed status edit must never stop the copy
            log.debug("progress notify failed", exc_info=True)

    async def _sleep(self, st: RunState, delay: float) -> None:
        end = time.monotonic() + delay
        while not (st.cancel or st.stop) and time.monotonic() < end:
            await asyncio.sleep(min(1.0, end - time.monotonic()))

    async def _run(self, job_id: int, st: RunState) -> None:
        try:
            job = dict(await self.db.job(job_id))
            opts = json.loads(job["opts"])
            plan = await self._plan(job, opts, st)
            if plan is None:  # cancelled while scanning
                await self.db.update_job(job_id, status="cancelled", finished_at=time.time())
                await self.notifier.finished(job_id)
                return
            fresh = await self.db.job(job_id)
            counters = {k: fresh[k] for k in ("copied", "skipped", "failed")}
            if opts.get("backend") == "user":
                st.bs = self.cfg.user_batch
            await self.db.update_job(job_id, status="running", error=None, started_at=job["started_at"] or time.time())
            await self._tick(job_id, st, force=True)
            clean = 0
            while True:
                await st.running.wait()
                if st.cancel or st.stop:
                    break
                window = plan.next(st.bs)
                if not window:
                    break
                try:
                    batch = await self._copy_ids(job, opts, window, st)
                except Stopped:
                    break
                if st.flood_until > time.monotonic():
                    clean = 0
                else:
                    clean += 1
                    if clean >= 8 and st.bs < self.cfg.batch_size:
                        st.bs, clean = min(self.cfg.batch_size, st.bs + 5), 0
                counters["copied"] += batch.copied
                if batch.copied:
                    counters["skipped"] += st.trailing + batch.skipped
                    st.trailing = 0
                else:
                    st.trailing += batch.skipped
                counters["failed"] += len(batch.failed)
                plan.advance(window, batch.copied)
                st.processed += len(window)
                st.samples.append((time.monotonic(), st.processed))
                extra = {} if st.retry is not None else {"pos": plan.pos}
                if plan.mode == "range":
                    extra["top_id"] = plan.last_hit
                await self.db.record_batch(job_id, batch.pairs, batch.failed, batch.windows, **counters, **extra)
                if batch.upgraded or batch.buttons_lost:
                    opts["upgraded"] = opts.get("upgraded", 0) + batch.upgraded
                    opts["buttons_lost"] = opts.get("buttons_lost", 0) + batch.buttons_lost
                    await self.db.update_job(job_id, opts=json.dumps(opts))
                await self._tick(job_id, st)
                await self._sleep(st, self.cfg.batch_delay)
            if st.stop and not st.cancel:
                return  # status stays 'running'; resume_all() continues from the saved position
            final = "cancelled" if st.cancel else "done"
            fields = {"status": final, "finished_at": time.time()}
            if plan.mode == "range" and not st.cancel:
                fields["top_id"] = plan.pos - 1  # last id attempted: later posts have higher ids
            await self.db.update_job(job_id, **fields)
            await self._tick(job_id, st, force=True)
            await self.notifier.finished(job_id)
            if final == "done":
                await self.drain_live(job_id, force=True)

        except asyncio.CancelledError:
            raise
        except FatalError as e:
            await self.db.update_job(job_id, status="error", error=str(e)[:500])
            await self._tick(job_id, st, force=True)
            await self.notifier.failed(job_id, str(e))
        except Exception as e:
            log.exception("job %s crashed", job_id)
            await self.db.update_job(job_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
            await self._tick(job_id, st, force=True)
            await self.notifier.failed(job_id, f"{type(e).__name__}: {e}")

    async def _plan(self, job: dict, opts: dict, st: RunState):
        if st.retry is not None:
            return ListPlan(st.retry)
        end = int(opts.get("to_id") or 0) or None
        if opts.get("backend") == "user":
            return await self._plan_user(job, opts, st, end)
        if job["started_at"] and job["mode"] == "range":  # resumed after a pause / restart
            if job["ids"]:
                st.known = set(json.loads(job["ids"]))
            if job["kbs"]:
                st.kbs = {int(k): v for k, v in json.loads(job["kbs"]).items()}
            look = 60 if job["ids"] else self.cfg.lookahead
            return RangePlan(job["start_pos"], job["total"], look, job["pos"], job["top_id"], end, st.known)
        lo = int(opts.get("from_id") or 1)
        await self.db.update_job(job["id"], status="scanning")
        await self._tick(job["id"], st, force=True)
        ids: list[int] = []
        keyboards: dict = {}
        if job["src_username"]:
            async def progress(found: int, oldest: int) -> None:
                await self.db.update_job(job["id"], total=found)
                await self._tick(job["id"], st)
                if st.cancel:
                    raise asyncio.CancelledError
            try:
                ids = await scrape.list_public_ids(job["src_username"], lo, end, self.cfg.scrape_delay, progress,
                                                   keyboards=keyboards)
            except asyncio.CancelledError:
                if st.cancel:
                    return None
                raise
            except Exception:
                log.warning("preview scan of @%s failed, falling back to a blind id range", job["src_username"], exc_info=True)
        hint = end or (ids[-1] if ids else 0) or int(opts.get("hint_top") or 0)
        st.known = set(ids) or None
        # with a preview the newest id is known (only unlisted album members can follow it); without, scan further
        look = 60 if ids else self.cfg.lookahead
        st.kbs = {i: rows for i, (rows, _) in keyboards.items() if rows} or None
        if keyboards:  # shown to the owner: URL buttons travel with the post, other kinds cannot
            opts["buttons_seen"] = len(st.kbs or {})
            opts["buttons_dropped"] = sum(dropped for _, dropped in keyboards.values())
        await self.db.update_job(job["id"], mode="range", ids=json.dumps(ids) if ids else None, total=hint, pos=lo,
                                 start_pos=lo, started_at=time.time(), opts=json.dumps(opts),
                                 kbs=json.dumps(st.kbs) if st.kbs else None)
        return RangePlan(lo, hint, look, end=end, known=st.known)

    async def _plan_user(self, job: dict, opts: dict, st: RunState, end: int | None):
        """Plan for the Premium-account engine: the account reads the exact list of posts and where albums are."""
        if job["ids"] and job["mode"] == "list":
            albums = {int(k): tuple(v) for k, v in (opts.get("albums") or {}).items()}
            ids = json.loads(job["ids"])
            if opts.get("fix_links") and opts.get("base"):
                st.predicted = {sid: opts["base"] + 1 + k for k, sid in enumerate(ids)}
            return ListPlan(ids, job["pos"], albums)
        if not self.user or not await self.user.connect():
            raise FatalError("اکانت پریمیوم به ربات متصل نیست (منوی اصلی ← 🌟 اکانت پریمیوم).")
        await self.db.update_job(job["id"], status="scanning")
        await self._tick(job["id"], st, force=True)

        async def progress(found: int, newest: int) -> None:
            await self.db.update_job(job["id"], total=found)
            await self._tick(job["id"], st)
            if st.cancel:
                raise asyncio.CancelledError
        try:
            async def on_flood(seconds: float) -> None:
                st.flood_until = time.monotonic() + seconds
                await self._tick(job["id"], st, force=True)
                await asyncio.sleep(seconds + 1)
            scanned = await self.user.scan(job["src"], int(opts.get("from_id") or 1), end, progress, on_flood)
        except asyncio.CancelledError:
            if st.cancel:
                return None
            raise
        ids, albums = scanned[0], scanned[1]
        blocked = list(scanned[2]) if len(scanned) > 2 else []
        opts["albums"] = {str(i): list(span) for i, span in albums.items()}
        if blocked:
            opts["blocked"] = blocked
        if opts.get("fix_links") and ids:
            # Links to posts that are not copied yet need their future ids: the destination hands out ids one after
            # another, so the k-th post will get base + 1 + k. Each window is checked against this while copying.
            opts["base"] = await self.user.probe_next_id(job["dst"])
            st.predicted = {sid: opts["base"] + 1 + k for k, sid in enumerate(ids)}
        await self.db.update_job(job["id"], mode="list", ids=json.dumps(ids), total=len(ids), pos=0, skipped=len(blocked),
                                 top_id=ids[-1] if ids else 0, started_at=time.time(), opts=json.dumps(opts))
        return ListPlan(ids, 0, albums)

    async def _copy_user(self, job: dict, opts: dict, ids: list[int], st: RunState, live: bool = False) -> Batch:
        await st.running.wait()
        if st.cancel or st.stop:
            raise Stopped
        engine = self
        done = await self.db.mapped(job["id"], ids)  # posts a half-finished earlier attempt already sent
        if done:
            ids = [i for i in ids if i not in done]
            if not ids:
                return Batch()

        class Hooks(UserHooks):
            async def flood(self, seconds: float) -> None:
                st.flood_until = time.monotonic() + seconds
                log.warning("job %s: Premium account flood wait %.0fs", job["id"], seconds)
                await engine.db.set_kv("user_flood_until", str(time.time() + seconds))  # a restart must not skip it
                await engine._tick(job["id"], st, force=True)
                if seconds >= 300:  # tell the owner once: this is Telegram's normal pacing, not a penalty
                    try:
                        await engine.notifier.notice(
                            job["id"], f"تلگرام برای {int(seconds // 60)} دقیقه سرعت نوشتن را محدود کرد.")
                    except Exception:
                        log.debug("notice failed", exc_info=True)
                await asyncio.sleep(seconds + 1)

            async def with_buttons(self, src_id: int, rows: list) -> int | None:
                try:
                    done = await engine._copy_plain(job, opts, [src_id], st, markup=markup_of(rows), check_stop=False)
                except (FatalError, Stopped):
                    return None
                return done.pairs[0][1] if done.pairs else None
        link_rewrite = None
        predicted = st.predicted if (st.predicted is not None and not live) else None
        if opts.get("fix_links") and (live or predicted is not None):  # re-point links at the new channel while copying
            from .linkfix import build_rules
            from .links import make_rewriter
            if live or st.rules is None:
                rules, target = await build_rules(self.db, await self.db.job(job["id"]), opts.get("link_names", []))
                if predicted is not None:
                    rules[0].mapping.update(predicted)   # forward links to posts that do not exist yet
                    st.rules = (rules, target)
            else:
                rules, target = st.rules
            link_rewrite = make_rewriter(rules, target)
        try:
            res = await self.user.copy_window(
                job["src"], job["dst"], ids, silent=bool(opts.get("silent", True)), protect=bool(opts.get("protect")),
                no_caption=bool(opts.get("no_caption")), upgrade=bool(opts.get("upgrade", True)),
                emoji=load_map(self.cfg.emoji_file or None, bool(opts.get("aliases"))), hooks=Hooks(), link_rewrite=link_rewrite)
        except BaseException as e:  # also a shutdown cancel: what was already sent must not be sent again
            partial = getattr(e, "partial", None)
            if partial and partial.pairs:  # keep what was sent before the error: a retry must not send it again
                fresh = await self.db.job(job["id"])
                await self.db.record_batch(job["id"], partial.pairs, partial.failed, [],
                                           copied=fresh["copied"] + len(partial.pairs))
            if isinstance(e, USER_FATAL):
                raise FatalError(f"{type(e).__name__}: {e}") from e
            raise
        if predicted is not None:
            wrong = [(s, d, predicted.get(s)) for s, d in res.pairs if predicted.get(s) != d]
            if wrong or res.failed or res.skipped or len(res.pairs) != len(ids):
                fresh = await self.db.job(job["id"])   # keep what was sent, then stop: later links would point to wrong posts
                await self.db.record_batch(job["id"], res.pairs, res.failed, [], copied=fresh["copied"] + len(res.pairs))
                raise FatalError(
                    "شماره‌گذاری پیش‌بینی‌شده با پست‌های ساخته‌شده نمی‌خواند "
                    f"(نمونه: پست {wrong[0][0]} در مقصد شماره‌ی {wrong[0][1]} گرفت، انتظار {wrong[0][2]}) "
                    if wrong else f"{len(res.failed) + res.skipped} پست این پنجره کپی نشد؛ لینک‌های بعدی غلط می‌شد، پس توقف کردم.")
        return Batch(pairs=res.pairs, failed=res.failed, copied=len(res.pairs), skipped=res.skipped,
                     upgraded=res.upgraded, buttons_lost=res.buttons_lost)

    async def _copy_ids(self, job: dict, opts: dict, ids: list[int], st: RunState, live: bool = False) -> Batch:
        """Copy a window. Posts with URL buttons go one by one with their buttons (Telegram's copy drops them)."""
        if opts.get("backend") == "user":
            return await self._copy_user(job, opts, ids, st, live)
        if not st.kbs or not any(i in st.kbs for i in ids):
            return await self._copy_plain(job, opts, ids, st)
        batch, segment, first = Batch(), [], True
        for i in ids:
            if i not in st.kbs:
                segment.append(i)
                continue
            if segment:
                batch.merge(await self._copy_plain(job, opts, segment, st, check_stop=first))
                segment, first = [], False
            batch.merge(await self._copy_plain(job, opts, [i], st, markup=markup_of(st.kbs[i]), check_stop=first))
            first = False
        if segment:
            batch.merge(await self._copy_plain(job, opts, segment, st, check_stop=first))
        return batch

    async def _copy_plain(self, job: dict, opts: dict, ids: list[int], st: RunState, markup=None,
                          check_stop: bool = True) -> Batch:
        """Copy `ids` in one call; on an unexplained error split the window until the bad message is isolated.

        `check_stop` is off for the later parts of a window already under way, so a shutdown never leaves a window
        half copied and unrecorded."""
        net_tries = 0
        while True:
            await st.running.wait()
            if check_stop and (st.cancel or st.stop):
                raise Stopped
            try:
                common = dict(chat_id=job["dst"], from_chat_id=job["src"],
                              disable_notification=bool(opts.get("silent", True)),
                              protect_content=True if opts.get("protect") else None, **SEND_TIMEOUTS)
                if markup is not None:
                    res = [await self.bot.copy_message(message_id=ids[0], reply_markup=markup, **common)]
                else:
                    res = await self.bot.copy_messages(message_ids=ids, remove_caption=True if opts.get("no_caption") else None,
                                                       **common)
            except RetryAfter as e:
                wait = seconds(e.retry_after) + 1
                st.flood_until = time.monotonic() + wait
                st.bs = max(5, st.bs // 2)
                log.warning("job %s: flood wait %.0fs, batch -> %d", job["id"], wait, st.bs)
                await self._tick(job["id"], st, force=True)
                await self._sleep(st, wait)
                continue
            except BadRequest as e:
                kind = classify(e)
                if kind == "fatal":
                    raise FatalError(str(e)) from e
                if kind == "empty":
                    return Batch(skipped=len(ids))
                if markup is not None and kind == "other":  # Telegram refused the buttons: still copy the post itself
                    log.warning("job %s: id %s: buttons rejected (%s), copying without them", job["id"], ids[0], e)
                    markup = None
                    continue
                if len(ids) == 1:
                    if kind == "unsupported":  # e.g. a "pinned a message" notice: nothing to copy, not a failure
                        log.info("job %s: id %s is not copyable (%s)", job["id"], ids[0], e)
                        return Batch(skipped=1)
                    return Batch(failed=[(ids[0], str(e))])
                mid = len(ids) // 2
                first = await self._copy_plain(job, opts, ids[:mid], st, check_stop=check_stop)
                return first.merge(await self._copy_plain(job, opts, ids[mid:], st, check_stop=False))
            except Forbidden as e:
                raise FatalError(str(e)) from e
            except (TimedOut, NetworkError) as e:  # BadRequest is a NetworkError too, so it is handled above
                net_tries += 1
                if net_tries > 4:
                    raise FatalError(f"Network keeps failing: {e}") from e
                await self._sleep(st, 5 * net_tries)
                continue
            dst = [m.message_id for m in res]
            seen = [i for i in ids if st.known and i in st.known]
            if len(dst) == len(ids):
                pairs = list(zip(ids, dst))
            elif seen and len(dst) == len(seen):  # the preview listed exactly the posts that exist here
                pairs = list(zip(seen, dst))
            else:
                # deleted / uncopyable ids inside the window: copied, but old->new pairing is only known per window
                return Batch(copied=len(dst), skipped=len(ids) - len(dst),
                             windows=[(ids[0], ids[-1], dst)] if dst else [])
            return Batch(pairs=pairs, copied=len(dst), skipped=len(ids) - len(dst))

    # live mirror --------------------------------------------------------------------------------------------
    def schedule_drain(self, job_id: int, delay: float = 3.0) -> None:
        async def later():
            await asyncio.sleep(delay)
            await self.drain_live(job_id)
        asyncio.create_task(later())

    async def drain_live(self, job_id: int, force: bool = False) -> None:
        """Copy posts that arrived in the source while the history was still being copied, or after it finished."""
        lock = self._drain_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            job = await self.db.job(job_id)
            if job and not job["mirror"]:  # switched off, or superseded by a newer job for the same pair
                await self.db.unqueue_live(job_id, [r["src_id"] for r in await self.db.live_queue(job_id)])
                return
            if not job or job["status"] != "done":
                return
            job, opts = dict(job), json.loads(job["opts"])
            rows = await self.db.live_queue(job_id)
            now = time.time()
            ready = [r for r in rows if force or now - r["ts"] >= 2.5]
            # posts the history pass already reached were copied by it (unless the pass missed them: not in the map)
            stale = [r["src_id"] for r in ready if r["src_id"] <= job["top_id"] and await self.db.was_copied(job_id, r["src_id"])]
            if stale:
                await self.db.unqueue_live(job_id, stale)
            ready = [r for r in ready if r["src_id"] not in set(stale)]
            groups: list[list[int]] = []
            last_grp = None
            for r in ready:  # an album is one group; a post with buttons and everything else is copied alone
                if r["grp"] and r["grp"] == last_grp and not r["markup"]:
                    groups[-1].append(r["src_id"])
                else:
                    groups.append([r["src_id"]])
                last_grp = r["grp"]
            st = RunState(bs=self.cfg.batch_size)
            st.kbs = {r["src_id"]: json.loads(r["markup"]) for r in ready if r["markup"]} or None
            st.running.set()
            for ids in groups:
                try:
                    batch = await self._copy_ids(job, opts, ids, st, live=True)
                except Stopped:
                    return
                except FatalError as e:
                    log.error("job %s: live copy stopped: %s", job_id, e)
                    await self.notifier.failed(job_id, str(e))
                    return
                await self.db.unqueue_live(job_id, ids)
                fresh = await self.db.job(job_id)
                await self.db.record_batch(job_id, batch.pairs, batch.failed, batch.windows,
                                           live=fresh["live"] + batch.copied, failed=fresh["failed"] + len(batch.failed))
            await self._tick(job_id, st, force=True)
