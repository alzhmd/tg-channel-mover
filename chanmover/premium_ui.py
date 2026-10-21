"""The "Premium account" screens: connect an account with a QR code, check it, disconnect.

Bots cannot put Premium emoji into channel posts (Telegram only allows it in private chats and groups), so the
Premium-account engine acts as a Premium user. The account is linked like Telegram Desktop: scan a QR code in
Settings -> Devices -> Link Desktop Device. No code or password is typed into the bot (a 2FA password is the only
exception, and the message is deleted right away)."""
from __future__ import annotations

import asyncio
import io
import logging
import time

import qrcode
from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Kb
from telegram import InputMediaPhoto, Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import ui
from .emoji import load_map

log = logging.getLogger("chanmover.premium")

INTRO = (
    "🌟 <b>اکانت پریمیوم</b>\n\n"
    "ربات‌ها (حتی اگر مالکشان پریمیوم باشد) نمی‌توانند در <b>کانال</b> ایموجی پریمیوم بگذارند؛ این محدودیتِ خودِ تلگرام است "
    "و فقط در چت خصوصی و گروه برداشته شده. برای اینکه ایموجی‌های ویژه‌ی پست‌ها حفظ شوند و ایموجی‌های معمولی هم "
    "<b>خودکار پریمیوم</b> شوند، انتقال باید با یک اکانت پریمیوم انجام شود که ادمین هر دو کانال است.\n\n"
    "اتصال مثل «Link Desktop Device» است و با اسکن QR انجام می‌شود؛ کد ورود را به کسی نمی‌دهید و هر وقت خواستید همین‌جا "
    "قطعش می‌کنید (نشست از «تنظیمات ← دستگاه‌ها» هم حذف می‌شود)."
)
NOT_CONFIGURED = (
    "🌟 <b>اکانت پریمیوم</b>\n\nاین ربات بدون اطلاعات برنامه‌ی تلگرام اجرا شده، پس موتور اکانت پریمیوم خاموش است. "
    "در فایل <code>.env</code> مقدارهای <code>TG_API_ID</code> و <code>TG_API_HASH</code> (از my.telegram.org) را بگذارید "
    "و ربات را ری‌استارت کنید."
)


def get_user(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("user")


def qr_png(url: str) -> io.BytesIO:
    buf = io.BytesIO()
    qrcode.make(url, box_size=8, border=3).save(buf, "PNG")
    buf.seek(0)
    buf.name = "login.png"
    return buf


def caption(url: str, with_link: bool = True) -> str:
    link = f'\n\nیا اگر روی همین گوشی هستید، <a href="{url}">این لینک</a> را بزنید.' if with_link else ""
    return ("📲 در تلگرام گوشی: <b>تنظیمات ← دستگاه‌ها ← اتصال دستگاه دسکتاپ</b> و این کد را اسکن کنید." + link +
            "\n\nکد هر ۳۰ ثانیه عوض می‌شود؛ تا ۵ دقیقه منتظرم.")


async def screen_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .handlers import show
    user = get_user(context)
    if not user:
        await show(update, NOT_CONFIGURED, ui.back_home())
        return
    info = await user.whoami()
    if not info:
        await show(update, INTRO, Kb([[Btn("🔗 اتصال با QR", callback_data="p:qr")], [Btn("🏠 منوی اصلی", callback_data="m:home")]]))
        return
    who = f"<b>{ui.esc(info['name'])}</b>" + (f" (@{ui.esc(info['username'])})" if info["username"] else "")
    status = "Premium ✅" if info["premium"] else "⚠️ این اکانت پریمیوم <b>نیست</b>؛ فقط کپی بدون نام انجام می‌شود، بدون ایموجی ویژه."
    text = (f"🌟 <b>اکانت پریمیوم</b>\n\n✅ متصل: {who}\n{status}\n\n"
            "در «➕ انتقال جدید» موتور «🌟 اکانت پریمیوم» انتخاب می‌شود (اگر اکانت پریمیوم باشد پیش‌فرض است). "
            "اکانت باید در کانال مبدا عضو و در کانال مقصد ادمین با دسترسی «ارسال پیام» باشد.")
    await show(update, text, Kb([[Btn("🔎 بررسی ایموجی ربات", callback_data="p:probe"), Btn("🔌 قطع اتصال", callback_data="p:out")],
                                 [Btn("🏠 منوی اصلی", callback_data="m:home")]]))


async def on_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .handlers import authorized, show
    if not await authorized(update, context):
        return
    q = update.callback_query
    await q.answer()
    user = get_user(context)
    action = q.data.split(":")[1]
    if not user:
        await show(update, NOT_CONFIGURED, ui.back_home())
    elif action == "qr":
        await start_qr(update, context)
    elif action == "out":
        await show(update, "🔌 اتصال اکانت قطع شود؟ جلسه‌ی ربات از «دستگاه‌ها»ی اکانت هم حذف می‌شود.",
                   Kb([[Btn("بله، قطع کن", callback_data="p:out2"), Btn("نه", callback_data="m:premium")]]))
    elif action == "out2":
        await user.logout()
        await screen_premium(update, context)
    elif action == "probe":
        await probe_owner(update, context)
    else:
        await screen_premium(update, context)


async def start_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(context)
    qr = await user.qr_login()
    chat_id = update.effective_chat.id
    msg = await context.bot.send_photo(chat_id, qr_png(qr.url), caption=caption(qr.url))
    old = context.application.bot_data.pop("qr_task", None)
    if old:
        old.cancel()
    context.application.bot_data["qr_task"] = asyncio.create_task(_wait_qr(context.application, user, qr, msg, chat_id))


async def _refresh_qr(bot, qr, msg, with_link: bool = True) -> bool:
    try:
        await bot.edit_message_media(chat_id=msg.chat_id, message_id=msg.message_id,
                                     media=InputMediaPhoto(qr_png(qr.url), caption=caption(qr.url, with_link), parse_mode="HTML"))
        return True
    except TelegramError as e:
        if "not modified" in str(e).lower():
            return True
        log.info("QR edit failed (%s)", e)
        return False


async def _wait_qr(app: Application, user, qr, msg, chat_id: int) -> None:
    from telethon import errors
    deadline = time.monotonic() + 300
    link = True
    try:
        while time.monotonic() < deadline:
            try:
                await qr.wait(timeout=25)
                break
            except TimeoutError:
                await qr.recreate()
                if not await _refresh_qr(app.bot, qr, msg, link):
                    link = False  # Telegram refused the tg:// link in the caption: keep the QR only
                    await _refresh_qr(app.bot, qr, msg, link)
            except errors.SessionPasswordNeededError:
                app.bot_data["pending_2fa"] = (chat_id, time.monotonic() + 600)  # survives menu taps, unlike a wizard state
                await app.bot.send_message(chat_id, "🔐 حساب شما رمز دومرحله‌ای دارد. همان رمز را همین‌جا بفرستید؛ پیامش فوراً پاک می‌شود.")
                return
        else:
            await app.bot.edit_message_caption(chat_id, msg.message_id, caption="⌛ کد منقضی شد. دوباره «🔗 اتصال با QR» را بزنید.")
            return
        await _connected(app, user, chat_id, msg)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("QR login failed")
        await app.bot.send_message(chat_id, "❌ اتصال انجام نشد. دوباره تلاش کنید.")


async def _connected(app: Application, user, chat_id: int, msg=None) -> None:
    await user.finish_login()
    info = await user.whoami()
    if msg:
        try:
            await app.bot.delete_message(chat_id, msg.message_id)
        except TelegramError:
            pass
    text = (f"✅ <b>{ui.esc(info['name'])}</b> متصل شد." +
            (" اکانت Premium است؛ ایموجی‌ها کامل منتقل می‌شوند." if info["premium"] else
             "\n⚠️ این اکانت Premium نیست؛ کپی بدون نام انجام می‌شود ولی ایموجی ویژه نمی‌گذارد."))
    await app.bot.send_message(chat_id, text, reply_markup=Kb([[Btn("➕ انتقال جدید", callback_data="m:new"),
                                                              Btn("🌟 اکانت", callback_data="m:premium")]]))


def waiting_for_password(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    pending = context.application.bot_data.get("pending_2fa")
    return bool(pending and pending[0] == chat_id and pending[1] > time.monotonic())


async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE, password: str) -> None:
    from telethon import errors
    user = get_user(context)
    msg = update.effective_message
    try:
        await msg.delete()
    except TelegramError:
        pass
    try:
        await user.client.sign_in(password=password)
    except errors.PasswordHashInvalidError:
        await context.bot.send_message(msg.chat_id, "❌ رمز اشتباه است؛ دوباره بفرستید.")
        return
    context.application.bot_data.pop("pending_2fa", None)
    await _connected(context.application, user, msg.chat_id)


async def probe_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the admin one test message with a Premium emoji and reports whether Telegram kept it, which tells whether
    the bot's owner has Premium. (Whatever the result, a bot still cannot keep Premium emoji in a channel post.)"""
    emoji = load_map().resolve("😀")
    chat_id = update.effective_chat.id
    sent = await context.bot.send_message(
        chat_id, "😀 آزمایش", entities=[{"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": emoji[1]}], parse_mode=None)
    kept = any(e.type == "custom_emoji" for e in (sent.entities or []))
    try:
        await sent.delete()
    except TelegramError:
        pass
    text = ("✅ مالک این ربات Premium است؛ ربات در چت خصوصی و گروه می‌تواند ایموجی پریمیوم بفرستد."
            if kept else "ℹ️ مالک این ربات Premium نیست (یا تلگرام نپذیرفت)؛ ربات ایموجی پریمیوم نمی‌فرستد.")
    await context.bot.send_message(chat_id, text + "\n\n⚠️ در <b>کانال</b> هیچ رباتی نمی‌تواند ایموجی پریمیوم بگذارد؛ برای کانال «🌟 اکانت پریمیوم» لازم است.")
