import asyncio
import json

from chanmover import scrape
from chanmover.engine import classify
from telegram.error import BadRequest, Forbidden

from conftest import FakeBot, Sent


async def wait_done(engine, job_id):
    await asyncio.wait_for(engine.state[job_id].task, 20)


async def test_range_scan_copies_existing_and_stops_after_lookahead(db, make):
    bot = FakeBot(existing=set(range(1, 26)) - {7, 8})
    engine, rec, job = await make(bot, {"hint_top": 25})
    await engine.start(job)
    await wait_done(engine, job)
    row = await db.job(job)
    assert row["status"] == "done" and rec.finished_jobs == [job]
    assert row["copied"] == 23
    assert sorted(bot.delivered) == sorted(bot.existing)
    assert max(c[-1] for c in bot.calls) <= 25 + 10 + 30  # stops a lookahead past the newest post (one window of slack)


async def test_exact_mapping_when_every_id_exists(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 31))

    async def fake_list(*a, **k):
        return list(range(1, 31))
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    pairs = {r["src_id"]: r["dst_id"] for r in await db.id_map(job)}
    assert len(pairs) == 30 and pairs[1] == 1001 and pairs[30] == 1030
    assert (await db.job(job))["mode"] == "range"
    assert bot.delivered == list(range(1, 31))  # oldest first, in order


async def test_flood_wait_is_retried_and_batch_shrinks(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 21), flood_first=1)

    async def fake_list(*a, **k):
        return list(range(1, 21))
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    assert (await db.job(job))["copied"] == 20
    assert len(bot.calls[0]) == 10 and len(bot.calls[1]) == 10  # same window retried after the flood wait
    assert len(bot.calls[2]) == 5  # the next window is smaller than before the flood


async def test_poison_message_is_isolated_not_the_whole_window(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 11), poison={4})

    async def fake_list(*a, **k):
        return list(range(1, 11))
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    row = await db.job(job)
    assert row["copied"] == 9 and row["failed"] == 1 and row["status"] == "done"
    assert [r["src_id"] for r in await db.failures(job)] == [4]
    assert sorted(bot.delivered) == [1, 2, 3, 5, 6, 7, 8, 9, 10]


async def test_resume_continues_from_saved_position_without_duplicates(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 41))

    async def fake_list(*a, **k):
        return list(range(1, 41))
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await asyncio.sleep(0)  # let it begin
    await engine.pause(job)
    await asyncio.sleep(0.05)
    # simulate a restart: new engine on the same DB, job still marked running
    await engine.cancel(job)
    await asyncio.wait_for(engine.state[job].task, 5)
    await db.update_job(job, status="running", finished_at=None)
    engine2 = type(engine)(bot, db, engine.cfg, rec)
    await engine2.resume_all()
    await wait_done(engine2, job)
    assert sorted(bot.delivered) == list(range(1, 41))
    assert len(bot.delivered) == len(set(bot.delivered))


async def test_fatal_error_stops_job_and_notifies(db, make, monkeypatch):
    class Kicked(FakeBot):
        async def copy_messages(self, *a, **k):
            raise Forbidden("Forbidden: bot was kicked from the channel chat")
    engine, rec, job = await make(Kicked(existing=[1]), {"hint_top": 1})
    await engine.start(job)
    await wait_done(engine, job)
    row = await db.job(job)
    assert row["status"] == "error" and "kicked" in row["error"] and rec.failed_jobs


async def test_live_queue_waits_for_history_then_copies_in_order(db, make):
    bot = FakeBot(existing=range(1, 120))
    engine, rec, job = await make(bot, {"hint_top": 5})
    await db.update_job(job, status="running")
    await db.queue_live(job, 101, None)
    await db.queue_live(job, 100, None)
    await engine.drain_live(job, force=True)
    assert bot.delivered == []  # job not done yet: nothing leaks into the middle of the history
    await db.update_job(job, status="done", top_id=50)
    await engine.drain_live(job, force=True)
    assert bot.delivered == [100, 101]
    assert (await db.job(job))["live"] == 2 and not await db.live_queue(job)


async def test_live_album_is_copied_as_one_call(db, make):
    bot = FakeBot(existing=range(1, 120))
    engine, rec, job = await make(bot)
    await db.update_job(job, status="done", top_id=50)
    for i in (90, 91, 92):
        await db.queue_live(job, i, "grpA")
    await db.queue_live(job, 93, None)
    await engine.drain_live(job, force=True)
    assert bot.calls == [[90, 91, 92], [93]]


def test_classify():
    assert classify(BadRequest("Bad Request: chat not found")) == "fatal"
    assert classify(BadRequest("Bad Request: not enough rights to send")) == "fatal"
    assert classify(BadRequest("Bad Request: message to copy not found")) == "empty"
    assert classify(BadRequest("Bad Request: something odd")) == "other"
    assert classify(BadRequest("Bad Request: there are no messages to forward")) == "empty"
    assert classify(BadRequest("Bad Request: message to forward not found")) == "fatal"
    assert classify(BadRequest("Bad Request: the message can't be copied")) == "unsupported"
    assert classify(BadRequest("Bad Request: messages can't be forwarded")) == "unsupported"


async def test_uncopyable_message_is_skipped_not_failed(db, make, monkeypatch):
    class Svc(FakeBot):
        async def copy_messages(self, chat_id, from_chat_id, message_ids, **kw):
            from telegram.error import BadRequest
            if 3 in message_ids:
                raise BadRequest("messages can't be forwarded")
            return await super().copy_messages(chat_id, from_chat_id, message_ids, **kw)
    bot = Svc(existing=range(1, 7))
    engine, rec, job = await make(bot, {"hint_top": 6})
    await engine.start(job)
    await wait_done(engine, job)
    row = await db.job(job)
    assert row["failed"] == 0 and row["copied"] == 5 and sorted(bot.delivered) == [1, 2, 4, 5, 6]


async def test_album_members_missing_from_preview_are_still_copied(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 13))  # 3-5 is an album: the preview lists only its first id

    async def fake_list(*a, **k):
        return [1, 2, 3, 6, 7, 8, 9, 10, 11, 12]
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    assert sorted(bot.delivered) == list(range(1, 13))
    assert (await db.job(job))["copied"] == 12


async def test_mapping_stays_exact_around_deleted_ids_when_preview_lists_them(db, make, monkeypatch):
    bot = FakeBot(existing=set(range(1, 11)) - {4, 5})

    async def fake_list(*a, **k):
        return [1, 2, 3, 6, 7, 8, 9, 10]
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    pairs = {r["src_id"]: r["dst_id"] for r in await db.id_map(job)}
    assert sorted(pairs) == [1, 2, 3, 6, 7, 8, 9, 10] and pairs[6] == 1004


async def test_mapping_is_not_guessed_when_unsure(db, make):
    bot = FakeBot(existing=set(range(1, 11)) - {4, 5})
    engine, rec, job = await make(bot, {"hint_top": 10})  # private source: no preview, gaps unexplained
    await engine.start(job)
    await wait_done(engine, job)
    assert await db.id_map(job) == []
    rows = await db.c.execute_fetchall("SELECT first_src,last_src,dst_ids FROM windows WHERE job_id=?", (job,))
    assert len(rows) == 1 and json.loads(rows[0][2]) == [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008]


async def test_preview_gaps_end_a_window_so_albums_map_exactly(db, make, monkeypatch):
    # 3-5 is an album (preview lists 3 only), 7 was deleted (not listed), the rest are single posts
    bot = FakeBot(existing={1, 2, 3, 4, 5, 6, 8, 9, 10})

    async def fake_list(*a, **k):
        return [1, 2, 3, 6, 8, 9, 10]
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    pairs = {r["src_id"]: r["dst_id"] for r in await db.id_map(job)}
    assert pairs == {1: 1001, 2: 1002, 3: 1003, 4: 1004, 5: 1005, 6: 1006, 8: 1007, 9: 1008, 10: 1009}
    assert (await db.job(job))["skipped"] == 1  # the deleted id 7; the empty tail is not counted


async def test_live_posts_inside_the_copied_range_are_not_duplicated(db, make):
    bot = FakeBot(existing=range(1, 200))
    engine, rec, job = await make(bot)
    await db.update_job(job, status="done", top_id=50)
    await db.c.execute("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", (job, 30, 7))
    await db.c.execute("INSERT INTO windows(job_id,first_src,last_src,dst_ids) VALUES(?,?,?,?)", (job, 40, 45, "[1]"))
    await db.c.commit()
    for i in (30, 41, 47, 60):  # mapped, inside an unpaired window, missed by the pass, newer than the pass
        await db.queue_live(job, i, None)
    await engine.drain_live(job, force=True)
    assert bot.delivered == [47, 60]


async def test_shutdown_finishes_the_window_and_the_next_start_loses_nothing(db, make, monkeypatch):
    bot = FakeBot(existing=range(1, 61))
    engine, rec, job = await make(bot, {"hint_top": 60})
    await engine.start(job)
    while not bot.calls:
        await asyncio.sleep(0)
    await engine.shutdown()
    row = await db.job(job)
    assert row["status"] == "running" and 0 < row["copied"] < 60
    engine2 = type(engine)(bot, db, engine.cfg, rec)
    await engine2.resume_all()
    await wait_done(engine2, job)
    assert sorted(bot.delivered) == list(range(1, 61)) and len(bot.delivered) == 60


async def test_only_the_newest_job_per_pair_mirrors(db, make):
    bot = FakeBot(existing=range(1, 5))
    engine, rec, first = await make(bot)
    second = await db.create_job(await db.get_chat(-100), await db.get_chat(-200), {"mirror": True}, 1)
    await db.update_job(first, status="done")
    await db.update_job(second, status="done")
    assert (await db.job(first))["mirror"] == 0
    assert [j["id"] for j in await db.mirror_jobs(-100)] == [second]
    await db.c.execute("UPDATE jobs SET mirror=1 WHERE id=?", (first,))  # even if both were on, only the newest mirrors
    await db.c.commit()
    assert [j["id"] for j in await db.mirror_jobs(-100)] == [second]


class MarkupBot(FakeBot):
    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None, **kw):
        self.next_id += 1
        self.delivered.append(message_id)
        self.markups = getattr(self, "markups", {})
        self.markups[message_id] = [[b.url for b in row] for row in reply_markup.inline_keyboard] if reply_markup else None
        return Sent(self.next_id)


async def test_button_posts_are_copied_alone_with_their_buttons(db, make, monkeypatch):
    bot = MarkupBot(existing=range(1, 9))

    async def fake_list(*a, keyboards=None, **k):
        keyboards.update({4: ([[("Watch", "https://t.me/bot?start=x")]], 1)})
        return list(range(1, 9))
    monkeypatch.setattr(scrape, "list_public_ids", fake_list)
    engine, rec, job = await make(bot, username="chan")
    await engine.start(job)
    await wait_done(engine, job)
    assert bot.markups == {4: [["https://t.me/bot?start=x"]]}
    assert sorted(bot.delivered) == list(range(1, 9))
    assert bot.calls[:2] == [[1, 2, 3], [5, 6, 7, 8]]  # plain windows on both sides of the button post
    pairs = {r["src_id"]: r["dst_id"] for r in await db.id_map(job)}
    assert sorted(pairs) == list(range(1, 9)) and pairs[4] == 1003 + 1 and pairs[5] == 1005
    row = await db.job(job)
    assert json.loads(row["opts"])["buttons_seen"] == 1 and json.loads(row["opts"])["buttons_dropped"] == 1
    assert row["copied"] == 8


async def test_live_post_with_buttons_keeps_them(db, make):
    bot = MarkupBot(existing=range(1, 120))
    engine, rec, job = await make(bot)
    await db.update_job(job, status="done", top_id=50)
    await db.queue_live(job, 90, None, json.dumps([[["Open", "https://example.com"]]]))
    await db.queue_live(job, 91, None, None)
    await engine.drain_live(job, force=True)
    assert bot.markups == {90: [["https://example.com"]]} and 91 in bot.delivered


class FakeUser:
    """Stands in for UserBackend: knows its posts and albums, 'copies' them to ids 5000+src."""

    def __init__(self, ids, albums=None, fatal=None):
        self.ids, self.albums, self.fatal = ids, albums or {}, fatal
        self.windows: list[list[int]] = []
        self.kwargs: dict = {}
        self.button_new = None

    async def connect(self):
        return True

    async def scan(self, src, lo, hi, progress):
        return list(self.ids), dict(self.albums)

    async def copy_window(self, src, dst, ids, hooks=None, **kw):
        from chanmover.userengine import Result
        if self.fatal:
            raise self.fatal
        self.windows.append(list(ids))
        self.kwargs = kw
        res = Result()
        for i in ids:
            if i == 13 and hooks:  # a post with URL buttons: the engine's bot carries it
                self.button_new = await hooks.with_buttons(i, [[["Watch", "https://t.me/bot?start=1"]]])
                res.pairs.append((i, self.button_new or 9999))
                continue
            res.pairs.append((i, 5000 + i))
        res.upgraded = len(ids) // 2
        return res


async def test_user_engine_never_splits_an_album_and_keeps_exact_pairs(db, make, cfg):
    user = FakeUser(ids=list(range(1, 31)), albums={i: (8, 12) for i in range(8, 13)})
    engine, rec, job = await make(MarkupBot(existing=range(1, 31)), {"backend": "user", "upgrade": True})
    engine.user = user
    await engine.start(job)
    await wait_done(engine, job)
    assert all(not (set(range(8, 13)) & set(w)) or set(range(8, 13)) <= set(w) for w in user.windows)  # album whole
    assert sum(len(w) for w in user.windows) == 30 and max(len(w) for w in user.windows) <= cfg.user_batch + 4
    pairs = {r["src_id"]: r["dst_id"] for r in await db.id_map(job)}
    assert len(pairs) == 30 and pairs[5] == 5005
    row = await db.job(job)
    assert row["status"] == "done" and row["copied"] == 30 and row["mode"] == "list"
    assert json.loads(row["opts"])["upgraded"] > 0 and user.kwargs["silent"] is True and user.kwargs["upgrade"] is True


async def test_user_engine_posts_with_buttons_go_through_the_bot(db, make):
    bot = MarkupBot(existing=range(1, 31))
    user = FakeUser(ids=list(range(1, 21)))
    engine, rec, job = await make(bot, {"backend": "user"})
    engine.user = user
    await engine.start(job)
    await wait_done(engine, job)
    assert bot.markups == {13: [["https://t.me/bot?start=1"]]} and user.button_new == 1001


async def test_user_engine_fatal_error_stops_the_job(db, make):
    from telethon import errors
    user = FakeUser(ids=[1, 2, 3], fatal=errors.ChatWriteForbiddenError(request=None))
    engine, rec, job = await make(FakeBot(existing=[]), {"backend": "user"})
    engine.user = user
    await engine.start(job)
    await wait_done(engine, job)
    assert (await db.job(job))["status"] == "error" and rec.failed_jobs


async def test_user_engine_without_a_connected_account_says_so(db, make):
    engine, rec, job = await make(FakeBot(existing=[]), {"backend": "user"})
    await engine.start(job)
    await wait_done(engine, job)
    row = await db.job(job)
    assert row["status"] == "error" and "اکانت پریمیوم" in row["error"]


async def test_user_engine_resumes_from_the_saved_list(db, make):
    user = FakeUser(ids=list(range(1, 41)))
    engine, rec, job = await make(FakeBot(existing=[]), {"backend": "user"})
    engine.user = user
    await db.update_job(job, ids=json.dumps(list(range(1, 41))), mode="list", total=40, pos=30, started_at=1.0)
    await engine.start(job)
    await wait_done(engine, job)
    assert [i for w in user.windows for i in w] == list(range(31, 41))


async def test_user_engine_does_not_resend_what_a_crashed_window_already_sent(db, make):
    class Crashy(FakeUser):
        crashed = False

        async def copy_window(self, src, dst, ids, hooks=None, **kw):
            from chanmover.userengine import Result
            if not Crashy.crashed and 4 in ids:
                Crashy.crashed = True
                err = RuntimeError("boom")
                err.partial = Result(pairs=[(i, 5000 + i) for i in ids if i < 4])
                raise err
            return await super().copy_window(src, dst, ids, hooks=hooks, **kw)

    user = Crashy(ids=list(range(1, 11)))
    engine, rec, job = await make(FakeBot(existing=[]), {"backend": "user"})
    engine.user = user
    await engine.start(job)
    await wait_done(engine, job)
    assert (await db.job(job))["status"] == "error"
    await engine.start(job)  # "continue"
    await wait_done(engine, job)
    sent = [i for w in user.windows for i in w]
    assert sorted(sent) == list(range(4, 11)) and len(sent) == len(set(sent))  # 1-3 were sent by the crashed attempt
    row = await db.job(job)
    assert row["status"] == "done" and row["copied"] == 10 and row["error"] is None
