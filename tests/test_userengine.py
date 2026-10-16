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
