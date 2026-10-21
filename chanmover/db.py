"""SQLite storage: known chats, admins, jobs, the old->new message map and failures."""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY, title TEXT, username TEXT, type TEXT,
    can_post INTEGER DEFAULT 0, is_member INTEGER DEFAULT 1, updated_at REAL, via TEXT DEFAULT 'bot');
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src INTEGER, dst INTEGER, src_title TEXT, dst_title TEXT, src_username TEXT,
    status TEXT DEFAULT 'new', opts TEXT DEFAULT '{}', ids TEXT,
    total INTEGER DEFAULT 0, pos INTEGER DEFAULT 0, start_pos INTEGER DEFAULT 0,
    copied INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, live INTEGER DEFAULT 0,
    top_id INTEGER DEFAULT 0, mirror INTEGER DEFAULT 1, mode TEXT DEFAULT 'list', kbs TEXT,
    owner INTEGER, status_chat INTEGER, status_msg INTEGER, error TEXT,
    created_at REAL, started_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS msgmap(
    job_id INTEGER, src_id INTEGER, dst_id INTEGER, PRIMARY KEY(job_id, src_id));
CREATE TABLE IF NOT EXISTS windows(
    job_id INTEGER, first_src INTEGER, last_src INTEGER, dst_ids TEXT);
CREATE TABLE IF NOT EXISTS failures(
    job_id INTEGER, src_id INTEGER, reason TEXT, PRIMARY KEY(job_id, src_id));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS fixes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, chat INTEGER, chat_title TEXT, status TEXT DEFAULT 'new',
    names TEXT DEFAULT '[]', ids TEXT, total INTEGER DEFAULT 0, pos INTEGER DEFAULT 0, edited INTEGER DEFAULT 0,
    links INTEGER DEFAULT 0, unmapped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, error TEXT, owner INTEGER,
    status_chat INTEGER, status_msg INTEGER, created_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS live_queue(
    job_id INTEGER, src_id INTEGER, grp TEXT, ts REAL, markup TEXT, PRIMARY KEY(job_id, src_id));
"""

JOB_FIELDS = {
    "status", "opts", "ids", "total", "pos", "start_pos", "copied", "skipped", "failed", "live", "top_id", "mirror",
    "mode", "status_chat", "status_msg", "error", "started_at", "finished_at", "kbs",
}


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.c = conn

    @classmethod
    async def open(cls, path: str) -> Database:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        migrations = (("jobs", "kbs TEXT"), ("live_queue", "markup TEXT"), ("chats", "via TEXT DEFAULT 'bot'"))
        for table, column in migrations:  # databases from older versions
            have = {r["name"] for r in await (await conn.execute(f"PRAGMA table_info({table})")).fetchall()}
            if column.split()[0] not in have:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.c.close()

    async def _one(self, sql: str, args: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.c.execute(sql, tuple(args)) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, args: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.c.execute(sql, tuple(args)) as cur:
            return list(await cur.fetchall())

    # link fixes -----------------------------------------------------------------------------------------------
    async def create_fix(self, job: aiosqlite.Row, names: list[str], owner: int, ids: list[int]) -> int:
        cur = await self.c.execute(
            "INSERT INTO fixes(job_id,chat,chat_title,names,ids,total,owner,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (job["id"], job["dst"], job["dst_title"], json.dumps(names), json.dumps(ids), len(ids), owner, time.time()))
        await self.c.commit()
        return cur.lastrowid

    async def fix(self, fix_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM fixes WHERE id=?", (fix_id,))

    async def fixes_for(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM fixes WHERE job_id=? ORDER BY id DESC", (job_id,))

    async def fixes_with_status(self, *statuses: str) -> list[aiosqlite.Row]:
        marks = ",".join("?" * len(statuses))
        return await self._all(f"SELECT * FROM fixes WHERE status IN ({marks}) ORDER BY id", statuses)

    async def update_fix(self, fix_id: int, **fields: Any) -> None:
        allowed = {"status", "pos", "edited", "links", "unmapped", "failed", "error", "status_chat", "status_msg",
                   "finished_at", "total", "ids"}
        bad = set(fields) - allowed
        if bad:
            raise KeyError(f"unknown fix fields: {sorted(bad)}")
        sets = ",".join(f"{k}=?" for k in fields)
        await self.c.execute(f"UPDATE fixes SET {sets} WHERE id=?", (*fields.values(), fix_id))
        await self.c.commit()

    async def src_to_dst(self, job_id: int) -> dict[int, int]:
        return {r["src_id"]: r["dst_id"] for r in await self._all(
            "SELECT src_id,dst_id FROM msgmap WHERE job_id=? AND dst_id IS NOT NULL", (job_id,))}

    async def earlier_job_into(self, chat_id: int, before_job: int) -> aiosqlite.Row | None:
        """The latest finished transfer whose destination is `chat_id` (the chain: old channel -> archive -> archive 2)."""
        return await self._one("SELECT * FROM jobs WHERE dst=? AND id<? AND status='done' AND copied>0 ORDER BY id DESC LIMIT 1",
                               (chat_id, before_job))

    # small settings -----------------------------------------------------------------------------------------
    async def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_kv(self, key: str, value: str) -> None:
        await self.c.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await self.c.commit()

    # admins -------------------------------------------------------------------------------------------------
    async def is_admin(self, user_id: int) -> bool:
        return await self._one("SELECT 1 FROM admins WHERE user_id=?", (user_id,)) is not None

    async def add_admin(self, user_id: int) -> None:
        await self.c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (user_id,))
        await self.c.commit()

    async def admin_ids(self) -> list[int]:
        return [r["user_id"] for r in await self._all("SELECT user_id FROM admins")]

    # chats --------------------------------------------------------------------------------------------------
    async def upsert_chat(self, chat_id: int, title: str, username: str | None, kind: str, can_post: bool,
                          is_member: bool = True, via: str = "bot") -> None:
        """`via` says who can see the chat: the bot, or only the connected Premium account."""
        await self.c.execute(
            "INSERT INTO chats(id,title,username,type,can_post,is_member,updated_at,via) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, username=excluded.username, type=excluded.type, "
            "can_post=excluded.can_post, is_member=excluded.is_member, updated_at=excluded.updated_at, via=excluded.via",
            (chat_id, title, username, kind, int(can_post), int(is_member), time.time(), via))
        await self.c.commit()

    async def get_chat(self, chat_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM chats WHERE id=?", (chat_id,))

    async def list_chats(self, writable: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM chats WHERE is_member=1" + (" AND can_post=1" if writable else "") + " ORDER BY title"
        return await self._all(sql)

    # jobs ---------------------------------------------------------------------------------------------------
    async def create_job(self, src: aiosqlite.Row, dst: aiosqlite.Row, opts: dict, owner: int) -> int:
        if opts.get("mirror", True):  # one live mirror per source->destination pair, or every new post is copied twice
            await self.c.execute("UPDATE jobs SET mirror=0 WHERE src=? AND dst=?", (src["id"], dst["id"]))
        cur = await self.c.execute(
            "INSERT INTO jobs(src,dst,src_title,dst_title,src_username,opts,mirror,owner,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (src["id"], dst["id"], src["title"], dst["title"], src["username"], json.dumps(opts),
             int(opts.get("mirror", True)), owner, time.time()))
        await self.c.commit()
        return cur.lastrowid

    async def job(self, job_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    async def jobs(self, limit: int = 20) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    async def jobs_with_status(self, *statuses: str) -> list[aiosqlite.Row]:
        marks = ",".join("?" * len(statuses))
        return await self._all(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id", statuses)

    async def mirror_jobs(self, src: int) -> list[aiosqlite.Row]:
        """Jobs mirroring new posts of `src`; only the newest job per destination, so a post is never copied twice."""
        rows = await self._all(
            "SELECT * FROM jobs WHERE src=? AND mirror=1 AND status IN ('scanning','running','paused','done') "
            "ORDER BY id DESC", (src,))
        newest: dict[int, aiosqlite.Row] = {}
        for r in rows:
            newest.setdefault(r["dst"], r)
        return list(newest.values())

    async def update_job(self, job_id: int, **fields: Any) -> None:
        bad = set(fields) - JOB_FIELDS
        if bad:
            raise KeyError(f"unknown job fields: {sorted(bad)}")
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
        await self.c.commit()

    async def record_batch(self, job_id: int, pairs: list[tuple[int, int | None]], failures: list[tuple[int, str]],
                           windows: list[tuple[int, int, list[int]]], **counters: Any) -> None:
        """Persist one copied window atomically: id map, failures, inexact-window note and the job counters."""
        await self.c.executemany("INSERT OR REPLACE INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)",
                                 [(job_id, s, d) for s, d in pairs])
        await self.c.executemany("INSERT OR REPLACE INTO failures(job_id,src_id,reason) VALUES(?,?,?)",
                                 [(job_id, s, r[:300]) for s, r in failures])
        await self.c.executemany("INSERT INTO windows(job_id,first_src,last_src,dst_ids) VALUES(?,?,?,?)",
                                 [(job_id, a, b, json.dumps(d)) for a, b, d in windows])
        bad = set(counters) - JOB_FIELDS
        if bad:
            raise KeyError(f"unknown job fields: {sorted(bad)}")
        if counters:
            sets = ",".join(f"{k}=?" for k in counters)
            await self.c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*counters.values(), job_id))
        await self.c.commit()

    async def was_copied(self, job_id: int, src_id: int) -> bool:
        """True when the id has a pair in the map, or lies in a window that was copied but not paired."""
        if await self._one("SELECT 1 FROM msgmap WHERE job_id=? AND src_id=?", (job_id, src_id)):
            return True
        return await self._one("SELECT 1 FROM windows WHERE job_id=? AND first_src<=? AND last_src>=?",
                               (job_id, src_id, src_id)) is not None

    async def mapped(self, job_id: int, ids: list[int]) -> set[int]:
        """Which of `ids` already have a copy recorded (a window interrupted half way is not sent twice)."""
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        rows = await self._all(f"SELECT src_id FROM msgmap WHERE job_id=? AND src_id IN ({marks}) AND dst_id IS NOT NULL",
                               (job_id, *ids))
        return {r["src_id"] for r in rows}

    async def id_map(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT src_id,dst_id FROM msgmap WHERE job_id=? ORDER BY src_id", (job_id,))

    async def failures(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT src_id,reason FROM failures WHERE job_id=? ORDER BY src_id", (job_id,))

    async def copied_dst_ids(self, job_id: int) -> list[int]:
        """Every message this job created in the destination (paired ones and the unpaired windows)."""
        ids = {r["dst_id"] for r in await self._all("SELECT dst_id FROM msgmap WHERE job_id=? AND dst_id IS NOT NULL", (job_id,))}
        for r in await self._all("SELECT dst_ids FROM windows WHERE job_id=?", (job_id,)):
            ids.update(json.loads(r["dst_ids"]))
        return sorted(ids)

    async def forget_job_copies(self, job_id: int) -> None:
        for table in ("msgmap", "windows", "failures", "live_queue"):
            await self.c.execute(f"DELETE FROM {table} WHERE job_id=?", (job_id,))
        await self.c.execute("UPDATE jobs SET copied=0, skipped=0, failed=0, live=0, pos=0, ids=NULL, mirror=0, "
                             "status='cancelled', error=NULL WHERE id=?", (job_id,))
        await self.c.commit()

    async def clear_failures(self, job_id: int, ids: list[int]) -> None:
        await self.c.executemany("DELETE FROM failures WHERE job_id=? AND src_id=?", [(job_id, i) for i in ids])
        await self.c.commit()

    # live queue ---------------------------------------------------------------------------------------------
    async def queue_live(self, job_id: int, src_id: int, grp: str | None, markup: str | None = None) -> None:
        await self.c.execute("INSERT OR IGNORE INTO live_queue(job_id,src_id,grp,ts,markup) VALUES(?,?,?,?,?)",
                             (job_id, src_id, grp, time.time(), markup))
        await self.c.commit()

    async def live_queue(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM live_queue WHERE job_id=? ORDER BY src_id", (job_id,))

    async def unqueue_live(self, job_id: int, ids: list[int]) -> None:
        await self.c.executemany("DELETE FROM live_queue WHERE job_id=? AND src_id=?", [(job_id, i) for i in ids])
        await self.c.commit()
