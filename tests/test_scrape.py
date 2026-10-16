import httpx

from chanmover import scrape


def page(ids, user="Chan"):
    return "".join(f'<div class="tgme_widget_message" data-post="{user}/{i}"></div>' for i in ids)


async def test_pagination_collects_everything_and_filters_range():
    pages = {None: page(range(81, 101)), "81": page(range(61, 81)), "61": page(range(41, 61)), "41": page(range(21, 41))}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.params.get("before"), ""))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ids = await scrape.list_public_ids("chan", lo=30, hi=None, delay=0, client=client)
    assert ids == list(range(30, 101))
    ids = await scrape.list_public_ids("chan", lo=1, hi=70, delay=0, client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text=pages.get(r.url.params.get("before"), page(range(61, 71))) if r.url.params.get("before") else page(range(61, 71))))))
    assert max(ids) <= 70


async def test_channel_without_preview_returns_empty():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>no posts</html>")))
    assert await scrape.list_public_ids("chan", delay=0, client=client) == []


def test_parse_ids_ignores_other_channels():
    assert scrape.parse_ids(page([3, 1, 2]) + page([9], "Other"), "chan") == [1, 2, 3]


def test_parse_keyboards():
    html = (
        '<div class="tgme_widget_message_wrap"><div data-post="Chan/5"></div><div class="tgme_widget_message_inline_keyboard">'
        '<div class="tgme_widget_message_inline_row">'
        '<a class="tgme_widget_message_inline_button url_button" href="https://t.me/bot?start=a&amp;b=1" target="_blank">'
        '<span class="tgme_widget_message_inline_button_text" dir="auto"><i class="emoji"><b>🎬</b></i> تماشا</span></a>'
        '<a class="tgme_widget_message_inline_button" href="https://t.me/Chan/5"><span class="tgme_widget_message_inline_button_text">cb</span></a>'
        '</div><div class="tgme_widget_message_inline_row">'
        '<a class="tgme_widget_message_inline_button url_button" href="https://example.com/"><span class="tgme_widget_message_inline_button_text">سایت</span></a>'
        '</div></div></div>'
        '<div class="tgme_widget_message_wrap"><div data-post="Chan/6"></div></div>')
    found = scrape.parse_keyboards(html, "chan")
    assert list(found) == [5]
    rows, dropped = found[5]
    assert rows == [[("🎬 تماشا", "https://t.me/bot?start=a&b=1")], [("سایت", "https://example.com/")]] and dropped == 1
