"""Link-fix runs: edit copied posts so that their links to the old channel's posts point at the new channel.

Runs with the Premium account (an admin can edit any post of the channel). Posts are only touched when a link really
changes, so a run can be repeated or resumed safely. Editing marks a post "edited" in the channel; that is Telegram's
doing and cannot be avoided.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from telethon import errors
from telethon.tl import types

from .db import Database
from .links import Rule, Target, rewrite
from .userengine import FATAL, Hooks, UserBackend

log = logging.getLogger("chanmover.linkfix")


def channel_number(chat_id: int) -> int:
    """123 for the chat id -100123 (the number used in t.me/c/123/45)."""
    return abs(chat_id) - 10**12 if chat_id < -10**12 else abs(chat_id)


async def build_rules(db: Database, job, extra_names: list[str]) -> tuple[list[Rule], Target]:
    """Rules for every old channel that feeds this transfer, following the chain (old -> archive -> archive 2)."""
    dst = await db.get_chat(job["dst"])
    target = Target(dst["username"] if dst else None, channel_number(job["dst"]))
    current = await db.src_to_dst(job["id"])
    rules: list[Rule] = []
    cur = job
    while cur is not None:
        src = await db.get_chat(cur["src"])
        names = {n.lower() for n in extra_names}
        if src and src["username"]:
            names.add(src["username"].lower())
        rules.append(Rule(names, {channel_number(cur["src"])}, current))
        earlier = await db.earlier_job_into(cur["src"], cur["id"])
        if earlier is None:
            break
        step = await db.src_to_dst(earlier["id"])
        current = {old: current[mid] for old, mid in step.items() if mid in current}   # old id -> id in the final channel
        cur = earlier
    return rules, target


@dataclass
class FixState:
    task: asyncio.Task | None = None
    running: asyncio.Event = field(default_factory=asyncio.Event)
    cancel: bool = False
    stop: bool = False
    last_tick: float = 0.0
    flood_until: float = 0.0


class FixNotifier:
    async def progress(self, fix_id: int) -> None: ...
    async def finished(self, fix_id: int) -> None: ...
    async def failed(self, fix_id: int, reason: str) -> None: ...
    async def notice(self, fix_id: int, text: str) -> None: ...


class LinkFixer:
    def __init__(self, db: Database, user: UserBackend, notifier: FixNotifier | None = None):
        self.db, self.user = db, user
        self.notifier = notifier or FixNotifier()
        self.state: dict[int, FixState] = {}

    # control ------------------------------------------------------------------------------------------------
    def is_active(self, fix_id: int) -> bool:
        st = self.state.get(fix_id)
        return bool(st and st.task and not st.task.done())

    async def create(self, job, owner: int, names: list[str] | None = None, last: int | None = None) -> int:
        """`last`: only the newest N posts (a trial run before touching the whole channel)."""
        ids = await self.db.copied_dst_ids(job["id"])
        if last:
            ids = ids[-last:]
        return await self.db.create_fix(job, [n.lstrip("@") for n in (names or [])], owner, ids)

    async def start(self, fix_id: int) -> None:
        if self.is_active(fix_id):
            return
        st = FixState()
        st.running.set()
        self.state[fix_id] = st
        st.task = asyncio.create_task(self._run(fix_id, st), name=f"fix-{fix_id}")

    async def pause(self, fix_id: int) -> None:
        st = self.state.get(fix_id)
        if st and self.is_active(fix_id):
            st.running.clear()
            await self.db.update_fix(fix_id, status="paused")
            await self._tick(fix_id, st, force=True)

    async def resume(self, fix_id: int) -> None:
        if self.is_active(fix_id):
            await self.db.update_fix(fix_id, status="running")
            self.state[fix_id].running.set()
        else:
            await self.start(fix_id)

    async def cancel(self, fix_id: int) -> None:
        st = self.state.get(fix_id)
        if st and self.is_active(fix_id):
            st.cancel = True
            st.running.set()
        else:
            await self.db.update_fix(fix_id, status="cancelled", finished_at=time.time())

    async def resume_all(self) -> None:
        for fix in await self.db.fixes_with_status("running"):
            await self.start(fix["id"])

    async def shutdown(self) -> None:
        tasks = [s.task for s in self.state.values() if s.task and not s.task.done()]
        for st in self.state.values():
            st.stop = True
            st.running.set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=60)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    # the run ------------------------------------------------------------------------------------------------
    async def _tick(self, fix_id: int, st: FixState, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - st.last_tick < 5:
            return
        st.last_tick = now
        try:
            await self.notifier.progress(fix_id)
        except Exception:
            log.debug("progress notify failed", exc_info=True)

    async def _run(self, fix_id: int, st: FixState) -> None:
        fixer = self

        class Flood(Hooks):
            async def flood(self, seconds: float) -> None:
                st.flood_until = time.monotonic() + seconds
                log.warning("fix %s: flood wait %.0fs", fix_id, seconds)
                await fixer.db.set_kv("user_flood_until", str(time.time() + seconds))
                await fixer._tick(fix_id, st, force=True)
                if seconds >= 300:
                    try:
                        await fixer.notifier.notice(fix_id, f"تلگرام برای {int(seconds // 60)} دقیقه سرعت ویرایش را محدود کرد.")
                    except Exception:
                        log.debug("notice failed", exc_info=True)
                await asyncio.sleep(seconds + 1)

        hooks = Flood()
        try:
            fix = dict(await self.db.fix(fix_id))
            job = await self.db.job(fix["job_id"])
            rules, target = await build_rules(self.db, job, json.loads(fix["names"]))
            if not await self.user.connect():
                raise RuntimeError("اکانت پریمیوم به ربات متصل نیست.")
            peer = await self.user.peer(fix["chat"])
            ids = json.loads(fix["ids"])
            pos, edited, links, unmapped, failed = fix["pos"], fix["edited"], fix["links"], fix["unmapped"], fix["failed"]
            await self.db.update_fix(fix_id, status="running", error=None)
            await self._tick(fix_id, st, force=True)
            while pos < len(ids):
                await st.running.wait()
                if st.cancel or st.stop:
                    break
                chunk = ids[pos:pos + 50]
                msgs = await self.user._call(hooks, lambda: self.user.client.get_messages(peer, ids=chunk))
                for m in msgs:
                    if m is None or isinstance(m, (types.MessageEmpty, types.MessageService)) or not m.entities:
                        continue
                    text, ents, rep = rewrite(m.raw_text or "", m.entities, rules, target)
                    links += rep.rewritten
                    unmapped += rep.unmapped
                    if not rep.rewritten:
                        continue
                    await self.user.pacer.before()
                    try:
                        await self.user._call(hooks, lambda: self.user.client.edit_message(
                            peer, m.id, text=text, formatting_entities=ents,
                            link_preview=isinstance(m.media, types.MessageMediaWebPage)))
                        edited += 1
                    except errors.MessageNotModifiedError:
                        pass
                    except FATAL:
                        raise
                    except errors.RPCError as e:
                        failed += 1
                        log.warning("fix %s: could not edit %s (%s)", fix_id, m.id, type(e).__name__)
                    finally:
                        self.user.pacer.after(1)
                pos += len(chunk)
                await self.db.update_fix(fix_id, pos=pos, edited=edited, links=links, unmapped=unmapped, failed=failed)
                await self._tick(fix_id, st)
                await asyncio.sleep(1.0)
            if st.stop and not st.cancel:
                return
            await self.db.update_fix(fix_id, status="cancelled" if st.cancel else "done", finished_at=time.time())
            await self._tick(fix_id, st, force=True)
            await self.notifier.finished(fix_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("fix %s crashed", fix_id)
            await self.db.update_fix(fix_id, status="error", error=f"{type(e).__name__}: {e}"[:400])
            await self._tick(fix_id, st, force=True)
            await self.notifier.failed(fix_id, f"{type(e).__name__}: {e}")
