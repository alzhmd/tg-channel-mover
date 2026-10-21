from types import SimpleNamespace

from chanmover import ui
from chanmover.handlers import authorized, can_post, parse_ref


def test_parse_ref():
    assert parse_ref("@my_channel") == "@my_channel"
    assert parse_ref("https://t.me/my_channel") == "@my_channel"
    assert parse_ref("t.me/my_channel/123") == "@my_channel"
    assert parse_ref("-1001234567890") == -1001234567890
    assert parse_ref("https://t.me/+AbCdEfGh123") == ("invite", "AbCdEfGh123")  # opened by the Premium account, never joined
    assert parse_ref("t.me/joinchat/AbCdEfGh123_-") == ("invite", "AbCdEfGh123_-")
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
           "opts": '{"buttons_seen": 7, "buttons_dropped": 2}'}
    text = ui.job_text(job, {"per_min": 100})
    assert "۷ پست" in text and "۲ دکمه" in text
    assert "۲۴۰" in text and "٪" in text and "⏳" in text and "@old_ch" in text
    kb = ui.job_keyboard(job, True)
    assert any("pause" in b.callback_data for row in kb.inline_keyboard for b in row)


async def test_listed_but_not_copied_posts_are_found(db, make_job):
    from chanmover.handlers import listed_not_copied
    job = await make_job(ids=[4, 5, 6, 7, 8], pos=9)
    await db.c.execute("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", (job["id"], 4, 1))
    await db.c.execute("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", (job["id"], 6, 2))
    await db.c.execute("INSERT INTO windows(job_id,first_src,last_src,dst_ids) VALUES(?,?,?,?)", (job["id"], 7, 8, "[3]"))
    await db.c.commit()
    assert await listed_not_copied(db, job) == [5]  # 7-8 sit in a window with unknown pairing, so they are not accused


async def test_channel_post_with_url_buttons_is_queued_with_them(db, cfg, make_job):
    import json

    from telegram import Bot, Update

    from chanmover.handlers import on_channel_post
    job = await make_job(ids=[1], pos=2)
    await db.update_job(job["id"], status="done")
    update = Update.de_json({"update_id": 1, "channel_post": {
        "message_id": 77, "date": 1790000000, "chat": {"id": -100, "type": "channel", "title": "src"}, "text": "hi",
        "reply_markup": {"inline_keyboard": [[{"text": "Watch", "url": "https://t.me/bot?start=1"}, {"text": "cb", "callback_data": "x"}],
                                              [{"text": "only cb", "callback_data": "y"}]]}}}, Bot("1:abc"))

    class Engine:
        def schedule_drain(self, *a, **k):
            pass
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"db": db, "cfg": cfg, "engine": Engine()}))
    await on_channel_post(update, ctx)
    row = (await db.live_queue(job["id"]))[0]
    assert row["src_id"] == 77 and json.loads(row["markup"]) == [[["Watch", "https://t.me/bot?start=1"]]]


async def test_undo_lists_every_post_the_job_created(db, make_job):
    job = await make_job(ids=[1, 2, 3], pos=3)
    await db.c.execute("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", (job["id"], 1, 100))
    await db.c.execute("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", (job["id"], 2, 101))
    await db.c.execute("INSERT INTO windows(job_id,first_src,last_src,dst_ids) VALUES(?,?,?,?)", (job["id"], 3, 9, "[102, 103]"))
    await db.c.commit()
    assert await db.copied_dst_ids(job["id"]) == [100, 101, 102, 103]
    await db.forget_job_copies(job["id"])
    row = await db.job(job["id"])
    assert await db.copied_dst_ids(job["id"]) == [] and row["status"] == "cancelled" and row["copied"] == 0 and not row["mirror"]


def test_parse_post_id():
    from chanmover.handlers import parse_post_id
    assert parse_post_id("https://t.me/OldChannel/12345") == 12345   # the "3" in the channel name is not an id
    assert parse_post_id("t.me/c/1111111111/987?single") == 987
    assert parse_post_id("۵۰۰۰") == 5000 and parse_post_id(" 77 ") == 77
    assert parse_post_id("آخرین") == "clear" and parse_post_id("همه") == "clear"
    assert parse_post_id("hello") is None
