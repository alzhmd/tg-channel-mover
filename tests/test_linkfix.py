import asyncio
from types import SimpleNamespace

from telethon.tl import types

from chanmover.linkfix import LinkFixer, build_rules, channel_number
from chanmover.userengine import Pacer

PM, A, B = -1002121858214, -1003941169817, -1004344149165


class FakeClient:
    def __init__(self, messages):
        self.messages, self.edits = messages, []

    async def get_messages(self, peer, ids):
        return [self.messages.get(i) for i in ids]

    async def edit_message(self, peer, msg_id, text, formatting_entities, link_preview):
        self.edits.append((msg_id, text, formatting_entities))
        self.messages[msg_id] = SimpleNamespace(id=msg_id, raw_text=text, entities=formatting_entities, media=None)


class FakeUser:
    def __init__(self, messages):
        self.client = FakeClient(messages)
        self.pacer = Pacer(0.0)

    async def connect(self):
        return True

    async def peer(self, chat_id):
        return chat_id

    async def _call(self, hooks, make):
        return await make()


def post(i, *urls):
    text = " ".join(f"film{n}" for n in range(len(urls))) or "plain"
    ents, off = [], 0
    for n, url in enumerate(urls):
        ents.append(types.MessageEntityTextUrl(offset=off, length=len(f"film{n}"), url=url))
        off += len(f"film{n}") + 1
    return SimpleNamespace(id=i, raw_text=text, entities=ents or None, media=None)


async def setup(db):
    await db.upsert_chat(PM, "pm", "Per3ianMoviezz", "channel", False)
    await db.upsert_chat(A, "A", None, "channel", True)
    await db.upsert_chat(B, "B", None, "channel", True)
    j4 = await db.create_job(await db.get_chat(PM), await db.get_chat(A), {"mirror": False}, 1)
    j6 = await db.create_job(await db.get_chat(A), await db.get_chat(B), {"mirror": False}, 1)
    for jid, pairs in ((j4, {100: 10, 200: 20, 300: 30}), (j6, {10: 1, 20: 2, 30: 3, 40: 4})):
        await db.c.executemany("INSERT INTO msgmap(job_id,src_id,dst_id) VALUES(?,?,?)", [(jid, s, d) for s, d in pairs.items()])
        await db.update_job(jid, status="done", copied=len(pairs))
    await db.c.commit()
    return j4, j6


async def test_rules_follow_the_chain_old_channel_to_archive_to_archive_two(db):
    j4, j6 = await setup(db)
    rules, target = await build_rules(db, await db.job(j6), ["Per3ianMoviez"])
    assert target.channel_id == channel_number(B) == 4344149165 and target.username is None
    by_chat = {frozenset(r.channel_ids): r for r in rules}
    assert by_chat[frozenset({channel_number(A)})].mapping == {10: 1, 20: 2, 30: 3, 40: 4}
    old = by_chat[frozenset({channel_number(PM)})]
    assert old.mapping == {100: 1, 200: 2, 300: 3} and {"per3ianmoviezz", "per3ianmoviez"} <= old.usernames


async def test_fixer_edits_only_posts_with_matching_links_and_resumes(db):
    j4, j6 = await setup(db)
    msgs = {10: post(10, "https://t.me/Per3ianMoviezz/100", "https://example.com"), 20: post(20),
            30: post(30, "https://t.me/Per3ianMoviezz/999", "https://t.me/Per3ianMoviez/300")}
    user = FakeUser(msgs)
    fixer = LinkFixer(db, user)
    fix_id = await fixer.create(await db.job(j4), 1, ["Per3ianMoviez"])
    await fixer.start(fix_id)
    await asyncio.wait_for(fixer.state[fix_id].task, 10)
    fix = await db.fix(fix_id)
    assert fix["status"] == "done" and fix["total"] == 3 and fix["pos"] == 3
    assert fix["edited"] == 2 and fix["links"] == 2 and fix["unmapped"] == 1
    urls = {e[0]: [x.url for x in e[2]] for e in user.client.edits}
    assert urls[10] == ["https://t.me/c/3941169817/10", "https://example.com"]      # same-id links: the archive's own post 10
    assert urls[30] == ["https://t.me/Per3ianMoviezz/999", "https://t.me/c/3941169817/30"]
    # a second run finds nothing left to change
    user.client.edits.clear()
    again = await fixer.create(await db.job(j4), 1, ["Per3ianMoviez"])
    await fixer.start(again)
    await asyncio.wait_for(fixer.state[again].task, 10)
    assert user.client.edits == [] and (await db.fix(again))["edited"] == 0
