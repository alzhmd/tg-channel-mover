"""SQLite storage: known chats, admins, jobs, the old->new message map and failures."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY, title TEXT, username TEXT, type TEXT,
    can_post INTEGER DEFAULT 0, is_member INTEGER DEFAULT 1, updated_at REAL);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src INTEGER, dst INTEGER, src_title TEXT, dst_title TEXT, src_username TEXT,
    status TEXT DEFAULT 'new', opts TEXT DEFAULT '{}', ids TEXT,
    total INTEGER DEFAULT 0, pos INTEGER DEFAULT 0, start_pos INTEGER DEFAULT 0,
    copied INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, live INTEGER DEFAULT 0,
    top_id INTEGER DEFAULT 0, mirror INTEGER DEFAULT 1, mode TEXT DEFAULT 'list',
    owner INTEGER, status_chat INTEGER, status_msg INTEGER, error TEXT,
    created_at REAL, started_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS msgmap(
    job_id INTEGER, src_id INTEGER, dst_id INTEGER, PRIMARY KEY(job_id, src_id));
CREATE TABLE IF NOT EXISTS windows(
    job_id INTEGER, first_src INTEGER, last_src INTEGER, dst_ids TEXT);
CREATE TABLE IF NOT EXISTS failures(
    job_id INTEGER, src_id INTEGER, reason TEXT, PRIMARY KEY(job_id, src_id));
CREATE TABLE IF NOT EXISTS live_queue(
    job_id INTEGER, src_id INTEGER, grp TEXT, ts REAL, PRIMARY KEY(job_id, src_id));
"""

JOB_FIELDS = {
    "status", "opts", "ids", "total", "pos", "start_pos", "copied", "skipped", "failed", "live", "top_id", "mirror",
    "mode", "status_chat", "status_msg", "error", "started_at", "finished_at",
}


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.c = conn

    @classmethod
    async def open(cls, path: str) -> "Database":
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
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
                          is_member: bool = True) -> None:
        await self.c.execute(
            "INSERT INTO chats(id,title,username,type,can_post,is_member,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, username=excluded.username, type=excluded.type, "
            "can_post=excluded.can_post, is_member=excluded.is_member, updated_at=excluded.updated_at",
            (chat_id, title, username, kind, int(can_post), int(is_member), time.time()))
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

    async def id_map(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT src_id,dst_id FROM msgmap WHERE job_id=? ORDER BY src_id", (job_id,))

    async def failures(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT src_id,reason FROM failures WHERE job_id=? ORDER BY src_id", (job_id,))

    async def clear_failures(self, job_id: int, ids: list[int]) -> None:
        await self.c.executemany("DELETE FROM failures WHERE job_id=? AND src_id=?", [(job_id, i) for i in ids])
        await self.c.commit()

    # live queue ---------------------------------------------------------------------------------------------
    async def queue_live(self, job_id: int, src_id: int, grp: str | None) -> None:
        await self.c.execute("INSERT OR IGNORE INTO live_queue(job_id,src_id,grp,ts) VALUES(?,?,?,?)",
                             (job_id, src_id, grp, time.time()))
        await self.c.commit()

    async def live_queue(self, job_id: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM live_queue WHERE job_id=? ORDER BY src_id", (job_id,))

    async def unqueue_live(self, job_id: int, ids: list[int]) -> None:
        await self.c.executemany("DELETE FROM live_queue WHERE job_id=? AND src_id=?", [(job_id, i) for i in ids])
        await self.c.commit()
