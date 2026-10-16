from chanmover.emoji import EmojiMap, u16, load_map

MAP = EmojiMap({"💥": "111", "🎖": "222", "1️⃣": "333", "👑": "444"}, {"🎥": "💥"})


def test_ordinary_emoji_become_custom_with_utf16_offsets():
    text = "فیلم 💥 امتیاز 🎖 : 4"
    new, spans = MAP.upgrade(text)
    assert new == text
    assert spans == [(u16("فیلم "), 2, "111"), (u16("فیلم 💥 امتیاز "), 2, "222")]


def test_existing_custom_code_and_zwj_are_left_alone():
    text = "💥 a 💥 b 👑"
    spans = MAP.upgrade(text, [(0, 2, "custom_emoji"), (5, 2, "code")])[1]
    assert [s[2] for s in spans] == ["444"]  # first is already custom, second sits in code
    assert MAP.upgrade("👑‍🔥")[1] == []  # part of a longer ZWJ sequence


def test_variation_selector_and_keycap():
    new, spans = MAP.upgrade("1️⃣ x")
    assert spans == [(0, 3, "333")]


def test_aliases_only_when_enabled():
    assert MAP.upgrade("🎥")[1] == []
    aliased = EmojiMap({"💥": "111"}, {"🎥": "💥"}, use_aliases=True)
    new, spans = aliased.upgrade("a 🎥 b")
    assert new == "a 💥 b" and spans == [(2, 2, "111")]


def test_limit_of_100_custom_emoji():
    assert len(MAP.upgrade("💥" * 150)[1]) == 100
    assert len(MAP.upgrade("💥" * 150, [(0, 2, "custom_emoji")] * 30)[1]) == 70


def test_bundled_map_is_valid():
    m = load_map()
    new, spans = m.upgrade("❤️ 💛")
    assert len(spans) == 2 and all(cid.isdigit() for _, _, cid in spans)
