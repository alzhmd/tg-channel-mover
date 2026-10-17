from types import SimpleNamespace

from telethon.tl import types

from chanmover.userengine import _entity_kind, url_rows


def button(text, url=None):
    return SimpleNamespace(text=text, url=url, data=None if url else b"x")


def test_url_rows_keeps_only_url_buttons():
    msg = SimpleNamespace(buttons=[[button("Watch", "https://t.me/bot?start=1"), button("cb")], [button("only cb")],
                                   [button("Site", "https://example.com/")]])
    assert url_rows(msg) == [[["Watch", "https://t.me/bot?start=1"]], [["Site", "https://example.com/"]]]
    assert url_rows(SimpleNamespace(buttons=None)) == []


def test_entity_kind_names():
    assert _entity_kind(types.MessageEntityCustomEmoji(offset=0, length=2, document_id=1)) == "custom_emoji"
    assert _entity_kind(types.MessageEntityCode(offset=0, length=2)) == "code"
    assert _entity_kind(types.MessageEntityBold(offset=0, length=2)) == "bold"


async def test_scan_waits_out_a_flood_and_continues_after_the_last_id():
    from telethon import errors
    from chanmover.emoji import EmojiMap
    from chanmover.userengine import UserBackend

    class Msg:
        def __init__(self, i, grouped=None):
            self.id, self.grouped_id = i, grouped

    class Client:
        calls = []

        async def get_input_entity(self, cid):
            return cid

        async def iter_messages(self, src, reverse, min_id, max_id, wait_time):
            self.calls.append(min_id)
            for i in range(min_id + 1, 11):
                if i == 6 and len(self.calls) == 1:
                    raise errors.FloodWaitError(request=None, capture=3)
                yield Msg(i, 77 if i in (8, 9) else None)

    waited = []

    async def on_flood(seconds):
        waited.append(seconds)

    ub = UserBackend.__new__(UserBackend)
    ub.client, ub._peers, ub.emoji = Client(), {}, EmojiMap({})
    ids, albums, blocked = await ub.scan(-100, 1, None, None, on_flood)
    assert blocked == [] and ids == list(range(1, 11)) and Client.calls == [0, 5] and waited == [3]
    assert albums == {8: (8, 9), 9: (8, 9)}


async def test_pacer_spaces_writes_and_backs_off_after_a_flood():
    import time
    from chanmover.userengine import Pacer
    p = Pacer(0.05)
    t = time.monotonic()
    for _ in range(3):
        await p.before()
        p.after(2)  # two posts -> at least 0.1 s before the next write
    assert time.monotonic() - t >= 0.19
    p.flooded(30)
    assert p.factor == 2.0
    p.flooded(1400)
    assert p.factor == 8.0  # x4 after a long wait, capped at 8
    p.after(1)
    assert 7.9 < p.factor < 8.0  # eases back slowly


async def test_a_restart_waits_out_a_known_flood():
    import time
    from chanmover.userengine import Pacer
    p = Pacer(0.0)
    p.hold_until(time.time() + 2, margin=0.5)
    t = time.monotonic()
    await p.before()
    assert time.monotonic() - t >= 2.2


def test_after_a_recent_flood_the_pace_starts_at_a_quarter_speed():
    import time
    from chanmover.userengine import Pacer
    p = Pacer(1.0)
    p.hold_until(time.time() - 600, margin=0)
    assert p.factor == 4.0
    fresh = Pacer(1.0)
    fresh.hold_until(0, margin=0)
    assert fresh.factor == 1.0
