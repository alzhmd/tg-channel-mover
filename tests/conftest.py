import dataclasses
import json

import pytest

from chanmover.config import Config
from chanmover.db import Database
from chanmover.engine import Engine, Notifier


@dataclasses.dataclass
class Sent:
    message_id: int


class FakeBot:
    """Stands in for telegram.Bot.copy_messages: `existing` ids are copyable, `poison` ids make a window fail."""

    def __init__(self, existing, poison=(), flood_first=0):
        self.existing, self.poison = set(existing), set(poison)
        self.flood_first = flood_first
        self.calls: list[list[int]] = []
        self.next_id = 1000
        self.delivered: list[int] = []

    async def copy_messages(self, chat_id, from_chat_id, message_ids, **kw):
        from telegram.error import BadRequest, RetryAfter
        self.calls.append(list(message_ids))
        if self.flood_first:
            self.flood_first -= 1
            raise RetryAfter(1)
        if self.poison & set(message_ids):
            raise BadRequest("Bad Request: something weird about this one")
        hits = [i for i in message_ids if i in self.existing]
        if not hits:
            raise BadRequest("Message to copy not found")
        out = []
        for i in hits:
            self.next_id += 1
            self.delivered.append(i)
            out.append(Sent(self.next_id))
        return tuple(out)


class Recorder(Notifier):
    def __init__(self):
        self.finished_jobs, self.failed_jobs = [], []

    async def finished(self, job_id):
        self.finished_jobs.append(job_id)

    async def failed(self, job_id, reason):
        self.failed_jobs.append((job_id, reason))


@pytest.fixture
def cfg():
    return Config(token="x", admin_ids=frozenset({1}), claim_code="", db_path=":memory:", batch_size=10,
                  batch_delay=0.0, lookahead=30, scrape_delay=0.0, api_id=1, api_hash="h", user_session="x",
                  emoji_file="", user_batch=10)


@pytest.fixture
async def db():
    d = await Database.open(":memory:")
    yield d
    await d.close()


@pytest.fixture
def make(db, cfg):
    async def _make(bot, opts=None, username=None):
        rec = Recorder()
        engine = Engine(bot, db, cfg, rec)
        await db.upsert_chat(-100, "src", username, "channel", False)
        await db.upsert_chat(-200, "dst", None, "channel", True)
        job_id = await db.create_job(await db.get_chat(-100), await db.get_chat(-200), opts or {}, 1)
        return engine, rec, job_id
    return _make


@pytest.fixture
def make_job(db):
    async def _make(ids, pos):
        await db.upsert_chat(-100, "src", None, "channel", False)
        await db.upsert_chat(-200, "dst", None, "channel", True)
        job_id = await db.create_job(await db.get_chat(-100), await db.get_chat(-200), {}, 1)
        await db.update_job(job_id, ids=json.dumps(ids), pos=pos)
        return await db.job(job_id)
    return _make
