from types import SimpleNamespace

from chanmover import ui
from chanmover.handlers import authorized, can_post, parse_ref


def test_parse_ref():
    assert parse_ref("@my_channel") == "@my_channel"
    assert parse_ref("https://t.me/my_channel") == "@my_channel"
    assert parse_ref("t.me/my_channel/123") == "@my_channel"
    assert parse_ref("-1001234567890") == -1001234567890
    assert parse_ref("https://t.me/+AbCdEfGh123") is None  # private invite links cannot be opened by a bot
    assert parse_ref("hello") is None


def test_can_post():
    admin = SimpleNamespace(status="administrator", can_post_messages=True)
    reader = SimpleNamespace(status="administrator", can_post_messages=False)
    assert can_post("channel", admin) and not can_post("channel", reader)
    assert can_post("supergroup", SimpleNamespace(status="member"))
    assert not can_post("channel", SimpleNamespace(status="member"))


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


async def test_only_listed_admins_get_in(db, cfg):
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"db": db, "cfg": cfg, "engine": None}))
    msg = FakeMessage()
    stranger = SimpleNamespace(effective_user=SimpleNamespace(id=999), callback_query=None, effective_message=msg)
    assert not await authorized(stranger, ctx)
    assert "999" in msg.sent[0]
    owner = SimpleNamespace(effective_user=SimpleNamespace(id=1), callback_query=None, effective_message=msg)
    assert await authorized(owner, ctx)
    await db.add_admin(999)
    assert await authorized(stranger, ctx)


def test_progress_text_for_a_running_job():
    job = {"id": 3, "status": "running", "src_title": "قدیمی", "src_username": "old_ch", "dst_title": "جدید", "total": 1000,
           "pos": 250, "start_pos": 1, "mode": "range", "copied": 240, "skipped": 10, "failed": 0, "live": 0, "mirror": 1,
           "error": None,
           "opts": '{"buttons_seen": 7}'}
    text = ui.job_text(job, {"per_min": 100})
    assert "۷ پست دکمه" in text
    assert "۲۴۰" in text and "٪" in text and "⏳" in text and "@old_ch" in text
    kb = ui.job_keyboard(job, True)
    assert any("pause" in b.callback_data for row in kb.inline_keyboard for b in row)
