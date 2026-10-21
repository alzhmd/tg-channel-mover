from telethon.tl import types

from chanmover.links import Report, Rule, Target, new_url, rewrite

RULE = Rule({"oldchannel", "oldname"}, {1111111111}, {4098: 9000, 1711: 9001, 12: 90})
TARGET = Target(None, 2222222222)


def test_new_url_handles_names_aliases_private_form_and_single():
    r = Report()
    assert new_url("https://t.me/OldChannel/4098", [RULE], TARGET, r) == "https://t.me/c/2222222222/9000"
    assert new_url("http://telegram.me/oldname/1711?single", [RULE], TARGET, r) == "https://t.me/c/2222222222/9001?single"
    assert new_url("https://t.me/c/1111111111/12?comment=5", [RULE], TARGET, r) == "https://t.me/c/2222222222/90"
    assert new_url("https://t.me/Other/4098", [RULE], TARGET, r) is None                 # another channel: untouched
    assert new_url("https://t.me/OldChannel/777", [RULE], TARGET, r) is None         # no copy of that post
    assert r.rewritten == 3 and r.unmapped == 1 and r.unmapped_ids == [777]
    assert new_url("https://t.me/OldChannel/4098", [RULE], Target("archive2", 1), Report()) == "https://t.me/archive2/9000"


def test_text_links_keep_every_other_entity():
    text = "فیلم A و فیلم B"
    ents = [types.MessageEntityBold(offset=0, length=4),
            types.MessageEntityTextUrl(offset=5, length=1, url="https://t.me/OldChannel/4098"),
            types.MessageEntityCustomEmoji(offset=7, length=1, document_id=5),
            types.MessageEntityTextUrl(offset=13, length=1, url="https://example.com/x")]
    new_text, new_ents, report = rewrite(text, ents, [RULE], TARGET)
    assert new_text == text and report.rewritten == 1
    assert [type(e).__name__ for e in new_ents] == ["MessageEntityBold", "MessageEntityTextUrl", "MessageEntityCustomEmoji",
                                                    "MessageEntityTextUrl"]
    assert new_ents[1].url == "https://t.me/c/2222222222/9000" and new_ents[3].url == "https://example.com/x"
    assert ents[1].url == "https://t.me/OldChannel/4098"      # the input is not modified


def test_bare_urls_are_replaced_in_the_text_and_later_entities_shift():
    url = "https://t.me/OldChannel/4098"
    text = f"😀 {url} بعد bold"
    start = len("😀 ".encode("utf-16-le")) // 2
    ents = [types.MessageEntityUrl(offset=start, length=len(url)),
            types.MessageEntityBold(offset=start + len(url) + 1, length=4)]       # "بعد " follows; bold covers "بعد "
    new_text, new_ents, report = rewrite(text, ents, [RULE], TARGET)
    new = "https://t.me/c/2222222222/9000"
    assert new_text == f"😀 {new} بعد bold" and report.rewritten == 1
    assert new_ents[0].offset == start and new_ents[0].length == len(new)
    assert new_ents[1].offset == start + len(new) + 1 and new_ents[1].length == 4
    assert new_text.encode("utf-16-le")[new_ents[1].offset * 2:(new_ents[1].offset + 4) * 2].decode("utf-16-le") == "بعد "


def test_nothing_to_do_returns_the_same_objects_content():
    ents = [types.MessageEntityBold(offset=0, length=2)]
    text, out, report = rewrite("hi there", ents, [RULE], TARGET)
    assert text == "hi there" and out == ents and report.rewritten == 0
