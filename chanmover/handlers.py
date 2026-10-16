"""Telegram handlers: admin menu, new-transfer wizard, job control and the live-mirror hooks."""
from __future__ import annotations

import csv
import io
import logging
import re

from telegram import BotCommand, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.constants import ChatMemberStatus as S, ChatType
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import ui
from .engine import Engine, Notifier
from .ui import esc, fa

log = logging.getLogger("chanmover.bot")

DEFAULT_OPTS = {"silent": True, "mirror": True, "protect": False, "no_caption": False,
                "from_id": None, "to_id": None, "hint_top": None}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
MEMBER_STATES = (S.ADMINISTRATOR, S.MEMBER, S.OWNER)
CHAT_TYPES = (ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP)


def deps(context: ContextTypes.DEFAULT_TYPE):
    d = context.application.bot_data
    return d["db"], d["engine"], d["cfg"]


async def authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    db, _, cfg = deps(context)
    user = update.effective_user
    if user and (user.id in cfg.admin_ids or await db.is_admin(user.id)):
        return True
    text = f"⛔ شما اجازه‌ی استفاده از این ربات را ندارید.\nشناسه‌ی عددی شما: <code>{user.id if user else '?'}</code>"
    if update.callback_query:
        await update.callback_query.answer("دسترسی ندارید", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(text)
    return False


async def show(update: Update, text: str, kb: Kb | None = None) -> None:
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, reply_markup=kb)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)


def draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("draft", {"src": None, "dst": None, "opts": dict(DEFAULT_OPTS)})


# ---------------------------------------------------------------------------------------------------- menus
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, cfg = deps(context)
    code = context.args[0] if context.args else ""
    user = update.effective_user
    if cfg.claim_code and code == cfg.claim_code and user and not await db.is_admin(user.id):
        await db.add_admin(user.id)
        await update.effective_message.reply_text("🔑 شما به‌عنوان مدیر ربات ثبت شدید.")
    if not await authorized(update, context):
        return
    context.user_data.pop("await", None)
    await show(update, ui.MAIN_TEXT, ui.main_keyboard())


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    q = update.callback_query
    await q.answer()
    db, _, _ = deps(context)
    where = q.data.split(":")[1]
    context.user_data.pop("await", None)
    if where == "home":
        await show(update, ui.MAIN_TEXT, ui.main_keyboard())
    elif where == "help":
        await show(update, ui.HELP_TEXT, ui.back_home())
    elif where == "new":
        context.user_data["draft"] = {"src": None, "dst": None, "opts": dict(DEFAULT_OPTS)}
        await screen_pick(update, context, "src")
    elif where == "jobs":
        await screen_jobs(update, context)
    elif where in ("chats", "chatsref"):
        if where == "chatsref":
            await refresh_chats(context)
        await screen_chats(update, context)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await authorized(update, context):
        await screen_jobs(update, context)


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await authorized(update, context):
        await screen_chats(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await authorized(update, context):
        await show(update, ui.HELP_TEXT, ui.back_home())


async def refresh_chats(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = deps(context)
    for row in await db.list_chats():
        try:
            chat = await context.bot.get_chat(row["id"])
            me = await context.bot.get_chat_member(row["id"], context.bot.id)
        except TelegramError:
            await db.upsert_chat(row["id"], row["title"], row["username"], row["type"], False, is_member=False)
            continue
        await db.upsert_chat(chat.id, chat.title or row["title"], chat.username, chat.type,
                             can_post(chat.type, me), is_member=me.status in MEMBER_STATES)


def can_post(kind: str, me) -> bool:
    if me.status == S.OWNER:
        return True
    if me.status != S.ADMINISTRATOR:
        return kind != ChatType.CHANNEL and me.status == S.MEMBER
    return True if kind != ChatType.CHANNEL else bool(getattr(me, "can_post_messages", False))


async def screen_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = deps(context)
    chats = await db.list_chats()
    if chats:
        lines = ["📡 <b>کانال‌های متصل به ربات</b>\n"]
        for c in chats:
            role = "✍️ مبدا و مقصد" if c["can_post"] else "👁 فقط مبدا (برای مقصد دسترسی «ارسال پیام» لازم است)"
            lines.append(f"• {ui.chat_label(c['title'], c['username'])}\n   {role}")
        text = "\n".join(lines)
    else:
        text = ("📡 هنوز در هیچ کانالی عضو نیستم.\n\nربات را از بخش «مدیران» هر کانال به‌عنوان <b>ادمین</b> اضافه کنید؛ "
                "به‌محض اضافه شدن همین‌جا خبر می‌دهم.")
    kb = Kb([[Btn("🔄 بررسی دوباره", callback_data="m:chatsref")], [Btn("🏠 منوی اصلی", callback_data="m:home")]])
    await show(update, text, kb)


async def screen_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = deps(context)
    jobs = await db.jobs(15)
    if not jobs:
        await show(update, "📋 هنوز انتقالی ثبت نشده.", Kb([[Btn("➕ انتقال جدید", callback_data="m:new")],
                                                           [Btn("🏠 منوی اصلی", callback_data="m:home")]]))
        return
    rows = []
    for j in jobs:
        icon = ui.STATUS.get(j["status"], "").split(" ")[0]
        rows.append([Btn(f"{icon} #{fa(j['id'])} {j['src_title'][:18]} ← {j['dst_title'][:18]} · {fa(j['copied'])}",
                         callback_data=f"j:{j['id']}:view")])
    rows.append([Btn("🏠 منوی اصلی", callback_data="m:home")])
    await show(update, "📋 <b>انتقال‌ها</b>", Kb(rows))


# ---------------------------------------------------------------------------------------------------- wizard
async def screen_pick(update: Update, context: ContextTypes.DEFAULT_TYPE, role: str) -> None:
    db, _, _ = deps(context)
    d = draft(context)
    chats = [c for c in await db.list_chats(writable=(role == "dst")) if c["id"] != d.get("src")]
    if role == "src":
        text = "۱️⃣ <b>کانال مبدا</b> را انتخاب کنید (همان که می‌خواهید کپی شود):"
    else:
        text = "۲️⃣ <b>کانال مقصد</b> را انتخاب کنید (جایی که پست‌ها ریخته می‌شود؛ ربات باید دسترسی «ارسال پیام» داشته باشد):"
    if not chats:
        text += "\n\n<i>هنوز کانالی نیست. ربات را ادمین کانال کنید یا با دکمه‌ی زیر کانال را معرفی کنید.</i>"
    rows = [[Btn(c["title"] + (f" · @{c['username']}" if c["username"] else ""), callback_data=f"w:{role}:{c['id']}")]
            for c in chats]
    rows.append([Btn("🔎 معرفی با یوزرنیم / لینک / فوروارد پست", callback_data=f"w:add:{role}")])
    rows.append([Btn("✖️ انصراف", callback_data="m:home")])
    await show(update, text, Kb(rows))


async def screen_opts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = deps(context)
    d = draft(context)
    o = d["opts"]
    src, dst = await db.get_chat(d["src"]), await db.get_chat(d["dst"])
    rng = "همه‌ی پست‌ها (۰ تا ۱۰۰٪)"
    if o["from_id"] or o["to_id"]:
        rng = f"از شناسه‌ی {fa(o['from_id'] or 1)} تا {fa(o['to_id']) if o['to_id'] else 'آخر'}"
    onoff = lambda v: "روشن ✅" if v else "خاموش"
    text = (f"۳️⃣ <b>تنظیمات انتقال</b>\n\n📤 مبدا: {ui.chat_label(src['title'], src['username'])}\n"
            f"📥 مقصد: {ui.chat_label(dst['title'], dst['username'])}\n\n"
            "نام فرستنده و «فوروارد از …» در همه‌ی حالت‌ها حذف می‌شود.")
    rows = [
        [Btn(f"📅 محدوده: {rng}", callback_data="w:range")],
        [Btn(f"🔕 ارسال بی‌صدا: {onoff(o['silent'])}", callback_data="w:t:silent")],
        [Btn(f"🔄 همگام‌سازی پست‌های جدید: {onoff(o['mirror'])}", callback_data="w:t:mirror")],
        [Btn(f"🔒 محافظت از محتوا در مقصد: {onoff(o['protect'])}", callback_data="w:t:protect")],
        [Btn(f"🚫 حذف کپشن‌ها: {onoff(o['no_caption'])}", callback_data="w:t:no_caption")],
    ]
    if not src["username"]:
        rows.append([Btn("📍 آخرین پست مبدا: " + (fa(o["hint_top"]) if o["hint_top"] else "تعیین نشده"), callback_data="w:hint")])
    rows.append([Btn("🚀 ادامه", callback_data="w:go"), Btn("✖️ انصراف", callback_data="m:home")])
    await show(update, text, Kb(rows))


async def on_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    q = update.callback_query
    await q.answer()
    db, engine, _ = deps(context)
    d = draft(context)
    parts = q.data.split(":")
    action = parts[1]
    if action in ("src", "dst"):
        d[action] = int(parts[2])
        if action == "src":
            await screen_pick(update, context, "dst")
        else:
            await screen_opts(update, context)
    elif action == "add":
        context.user_data["await"] = {"kind": "chat", "role": parts[2]}
        await show(update, "🔎 یوزرنیم (<code>@name</code>)، لینک عمومی (<code>t.me/name</code>) یا شناسه‌ی عددی کانال را بفرستید، "
                           "یا <b>یکی از پست‌های کانال را همین‌جا فوروارد کنید</b>.\n\n"
                           "ℹ️ ربات باید قبلاً ادمین آن کانال شده باشد؛ لینک دعوت خصوصی را ربات نمی‌تواند باز کند.",
                   Kb([[Btn("✖️ انصراف", callback_data="m:home")]]))
    elif action == "t":
        d["opts"][parts[2]] = not d["opts"][parts[2]]
        await screen_opts(update, context)
    elif action == "range":
        context.user_data["await"] = {"kind": "range"}
        await show(update, "📅 شناسه‌ی پست شروع و پایان را بفرستید، مثل <code>100 2500</code>.\n"
                           "فقط شروع: <code>100</code> · همه‌ی پست‌ها: <code>همه</code>\n"
                           "(شناسه همان عدد آخر لینک پست است: t.me/name/<b>123</b>)",
                   Kb([[Btn("↩️ برگشت", callback_data="w:back")]]))
    elif action == "hint":
        context.user_data["await"] = {"kind": "hint"}
        await show(update, "📍 آخرین پست کانال مبدا را همین‌جا <b>فوروارد کنید</b> یا شماره‌ی آن را بفرستید؛ "
                           "برای مبدای خصوصی، ربات با آن می‌فهمد تا کجا باید برود.",
                   Kb([[Btn("↩️ برگشت", callback_data="w:back")]]))
    elif action == "back":
        context.user_data.pop("await", None)
        await screen_opts(update, context)
    elif action == "go":
        await screen_confirm(update, context)
    elif action == "run":
        await start_job(update, context)


async def screen_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = deps(context)
    d = draft(context)
    src, dst = await db.get_chat(d["src"]), await db.get_chat(d["dst"])
    dup = [j for j in await db.jobs(50) if j["src"] == d["src"] and j["dst"] == d["dst"] and j["status"] in ("done", "running", "paused", "scanning")]
    warn = ""
    if dup:
        warn = (f"\n\n⚠️ قبلاً انتقال #{fa(dup[0]['id'])} از همین مبدا به همین مقصد ثبت شده؛ اگر دوباره شروع کنید "
                "پست‌ها <b>تکراری</b> می‌شوند.")
    text = (f"❓ <b>مطمئنید؟</b>\n\nهمه‌ی پست‌های {ui.chat_label(src['title'], src['username'])} در "
            f"{ui.chat_label(dst['title'], dst['username'])} ارسال می‌شود. ارسال پست قابل برگشت نیست."
            "\n\nپیشنهاد: مقصد را تا پایان کار عمومی نکنید." + warn)
    await show(update, text, Kb([[Btn("✅ بله، شروع کن", callback_data="w:run")], [Btn("↩️ برگشت", callback_data="w:back")]]))


async def preflight(context: ContextTypes.DEFAULT_TYPE, d: dict) -> str | None:
    """Checks the bot can really read the source and post to the destination, and refreshes their stored info
    (title / username may have changed since the bot was added)."""
    bot = context.bot
    db, _, _ = deps(context)
    for label, cid in (("مبدا", d["src"]), ("مقصد", d["dst"])):
        try:
            chat = await bot.get_chat(cid)
            me = await bot.get_chat_member(cid, bot.id)
        except TelegramError as e:
            return f"ربات به کانال {label} دسترسی ندارد ({e}). دوباره ادمینش کنید."
        if me.status not in MEMBER_STATES:
            return f"ربات دیگر عضو کانال {label} نیست."
        if label == "مقصد" and not can_post(chat.type, me):
            return "ربات در مقصد دسترسی «ارسال پیام» ندارد."
        await db.upsert_chat(chat.id, chat.title or str(chat.id), chat.username, chat.type, can_post(chat.type, me))
        if label == "مبدا" and getattr(chat, "has_protected_content", False):
            return "کانال مبدا «محدودیت ذخیره و فوروارد» دارد و تلگرام اجازه‌ی کپی پست‌هایش را نمی‌دهد. اول آن را خاموش کنید."
    return None


async def start_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, engine, _ = deps(context)
    d = draft(context)
    if not d.get("src") or not d.get("dst"):
        await show(update, "جلسه‌ی ساخت منقضی شد؛ از اول شروع کنید.", ui.main_keyboard())
        return
    err = await preflight(context, d)
    if err:
        await show(update, f"❌ {esc(err)}", Kb([[Btn("↩️ برگشت", callback_data="w:back")]]))
        return
    src, dst = await db.get_chat(d["src"]), await db.get_chat(d["dst"])
    job_id = await db.create_job(src, dst, d["opts"], update.effective_user.id)
    context.user_data.pop("draft", None)
    msg = update.callback_query.message
    await db.update_job(job_id, status_chat=msg.chat_id, status_msg=msg.message_id)
    await engine.start(job_id)
    await show_job(update, context, job_id)


# ---------------------------------------------------------------------------------------------------- text input
def parse_ref(text: str):
    text = text.strip()
    if re.search(r"t\.me/(\+|joinchat/)", text):
        return None
    m = re.search(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,31})", text)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"-?\d{6,15}", text):
        return int(text)
    return None


async def register_chat(context: ContextTypes.DEFAULT_TYPE, ref) -> tuple[object | None, str | None]:
    db, _, _ = deps(context)
    try:
        chat = await context.bot.get_chat(ref)
    except TelegramError:
        return None, "کانالی با این مشخصات پیدا نشد. یوزرنیم را بررسی کنید، یا یکی از پست‌هایش را فوروارد کنید."
    if chat.type not in CHAT_TYPES:
        return None, "این یک کانال یا گروه نیست."
    try:
        me = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError:
        me = None
    if not me or me.status not in MEMBER_STATES:
        return None, ("ربات عضو این کانال نیست؛ بدون عضویت نمی‌تواند پست‌ها را بخواند. ربات را ادمین کانال کنید و دوباره تلاش کنید.")
    await db.upsert_chat(chat.id, chat.title or str(chat.id), chat.username, chat.type, can_post(chat.type, me))
    return await db.get_chat(chat.id), None


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    msg = update.effective_message
    aw = context.user_data.get("await")
    if not aw:
        await show(update, ui.MAIN_TEXT, ui.main_keyboard())
        return
    d = draft(context)
    origin = getattr(msg, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None)
    text = (msg.text or "").translate(_DIGITS).strip()
    if aw["kind"] == "chat":
        ref = origin_chat.id if origin_chat else parse_ref(text)
        if ref is None:
            await msg.reply_text("نفهمیدم. یوزرنیم کانال (<code>@name</code>) یا لینک عمومی‌اش را بفرستید، یا یک پست را فوروارد کنید.\n"
                                 "لینک دعوت خصوصی (<code>t.me/+…</code>) را نمی‌توانم باز کنم؛ ربات را ادمین کانال کنید.")
            return
        row, err = await register_chat(context, ref)
        if err:
            await msg.reply_text(f"❌ {err}")
            return
        context.user_data.pop("await", None)
        role = aw["role"]
        if role == "dst" and not row["can_post"]:
            await msg.reply_text("⚠️ ربات در این کانال دسترسی «ارسال پیام» ندارد؛ اول دسترسی بدهید.")
            await screen_pick(update, context, "dst")
            return
        d[role] = row["id"]
        if role == "src":
            if origin_chat and getattr(origin, "message_id", None):
                d["opts"]["hint_top"] = origin.message_id
            await screen_pick(update, context, "dst")
        else:
            await screen_opts(update, context)
    elif aw["kind"] == "range":
        context.user_data.pop("await", None)
        nums = [int(x) for x in re.findall(r"\d+", text)]
        d["opts"]["from_id"] = nums[0] if nums else None
        d["opts"]["to_id"] = nums[1] if len(nums) > 1 else None
        await screen_opts(update, context)
    elif aw["kind"] == "hint":
        num = getattr(origin, "message_id", None) or next((int(x) for x in re.findall(r"\d+", text)), None)
        if not num:
            await msg.reply_text("عدد یا یک پست فورواردشده بفرستید.")
            return
        context.user_data.pop("await", None)
        d["opts"]["hint_top"] = num
        await screen_opts(update, context)


# ---------------------------------------------------------------------------------------------------- jobs
async def show_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, follow: bool = False) -> None:
    db, engine, _ = deps(context)
    job = await db.job(job_id)
    if follow and update.callback_query:  # progress edits now go to the message the owner is looking at
        m = update.callback_query.message
        await db.update_job(job_id, status_chat=m.chat_id, status_msg=m.message_id)
        job = await db.job(job_id)
    await show(update, ui.job_text(job, engine.stats(job_id)), ui.job_keyboard(job, engine.is_active(job_id)))


async def on_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorized(update, context):
        return
    q = update.callback_query
    db, engine, _ = deps(context)
    _, jid, action = q.data.split(":")
    job_id = int(jid)
    job = await db.job(job_id)
    if not job:
        await q.answer("پیدا نشد", show_alert=True)
        return
    await q.answer()
    if action == "view":
        await show_job(update, context, job_id, follow=True)
    elif action == "pause":
        await engine.pause(job_id)
        await show_job(update, context, job_id)
    elif action == "resume":
        await engine.resume(job_id)
        await show_job(update, context, job_id, follow=True)
    elif action == "cancel":
        await show(update, f"⛔ انتقال #{fa(job_id)} لغو شود؟ آنچه تا حالا کپی شده در مقصد می‌ماند.",
                   Kb([[Btn("بله، لغو کن", callback_data=f"j:{job_id}:cancel2"), Btn("نه", callback_data=f"j:{job_id}:view")]]))
    elif action == "cancel2":
        await engine.cancel(job_id)
        await show_job(update, context, job_id)
    elif action == "mirror":
        if not job["mirror"]:  # turning it on here turns it off on older jobs for the same pair
            await db.c.execute("UPDATE jobs SET mirror=0 WHERE src=? AND dst=?", (job["src"], job["dst"]))
        await db.update_job(job_id, mirror=0 if job["mirror"] else 1)
        if not job["mirror"] and job["status"] == "done":
            engine.schedule_drain(job_id, 0.5)
        await show_job(update, context, job_id)
    elif action == "retry":
        fails = [r["src_id"] for r in await db.failures(job_id)]
        if fails:
            await db.clear_failures(job_id, fails)
            await db.update_job(job_id, failed=0, status="running", finished_at=None)
            await engine.start(job_id, retry=fails)
        await show_job(update, context, job_id, follow=True)
    elif action == "fails":
        rows = await db.failures(job_id)
        lines = [f"⚠️ <b>پست‌های ناموفقِ انتقال #{fa(job_id)}</b> ({fa(len(rows))})\n"]
        for r in rows[:40]:
            lines.append(f"• <a href=\"{ui.post_link(job['src'], job['src_username'], r['src_id'])}\">{fa(r['src_id'])}</a>: "
                         f"<code>{esc(r['reason'][:90])}</code>")
        if len(rows) > 40:
            lines.append(f"… و {fa(len(rows) - 40)} مورد دیگر")
        await q.message.reply_text("\n".join(lines))
    elif action == "export":
        await send_export(update, context, job)


async def send_export(update: Update, context: ContextTypes.DEFAULT_TYPE, job) -> None:
    db, _, _ = deps(context)
    dst = await db.get_chat(job["dst"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["old_id", "new_id", "old_link", "new_link"])
    rows = await db.id_map(job["id"])
    for r in rows:
        w.writerow([r["src_id"], r["dst_id"], ui.post_link(job["src"], job["src_username"], r["src_id"]),
                    ui.post_link(job["dst"], dst["username"] if dst else None, r["dst_id"])])
    n_windows = (await db.c.execute_fetchall("SELECT COUNT(*) FROM windows WHERE job_id=?", (job["id"],)))[0][0]
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    data.name = f"job{job['id']}_old_to_new.csv"
    note = f"\n⚠️ برای {fa(n_windows)} بازه فقط ترتیب کلی مشخص است (پست‌هایی که پاک شده یا کپی نشده‌اند وسطشان بوده)." if n_windows else ""
    await update.callback_query.message.reply_document(data, caption=f"📤 جدول قدیم ← جدید ({fa(len(rows))} پست){note}")


class UiNotifier(Notifier):
    def __init__(self, app: Application):
        self.app = app
        self.engine: Engine | None = None

    @property
    def db(self):
        return self.app.bot_data["db"]

    async def progress(self, job_id: int) -> None:
        job = await self.db.job(job_id)
        if not job or not job["status_msg"]:
            return
        try:
            await self.app.bot.edit_message_text(
                ui.job_text(job, self.engine.stats(job_id)), chat_id=job["status_chat"], message_id=job["status_msg"],
                reply_markup=ui.job_keyboard(job, self.engine.is_active(job_id)))
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                log.debug("progress edit failed: %s", e)
        except RetryAfter:
            pass

    async def finished(self, job_id: int) -> None:
        job = await self.db.job(job_id)
        if not job:
            return
        head = {"done": "✅ انتقال کامل شد", "cancelled": "⛔ انتقال لغو شد"}.get(job["status"], "ℹ️ انتقال پایان یافت")
        text = (f"{head} — #{fa(job_id)}\n{ui.chat_label(job['src_title'])} ← {ui.chat_label(job['dst_title'])}\n\n"
                f"✅ کپی‌شده: <b>{fa(job['copied'])}</b>\n⏭ ردشده: {fa(job['skipped'])}\n⚠️ ناموفق: {fa(job['failed'])}")
        if job["status"] == "done" and job["mirror"]:
            text += "\n\n🔄 همگام‌سازی زنده روشن است؛ پست‌های جدید مبدا خودکار منتقل می‌شوند."
        await self.app.bot.send_message(job["owner"], text, reply_markup=Kb(
            [[Btn("📊 جزئیات", callback_data=f"j:{job_id}:view"), Btn("📤 خروجی قدیم←جدید", callback_data=f"j:{job_id}:export")]]))

    async def failed(self, job_id: int, reason: str) -> None:
        job = await self.db.job(job_id)
        if job:
            await self.app.bot.send_message(
                job["owner"], f"❌ انتقال #{fa(job_id)} متوقف شد:\n<code>{esc(reason[:200])}</code>\n\n{ui.explain_error(reason)}\n"
                              "آنچه تا اینجا کپی شده سر جایش می‌ماند و ادامه از همان نقطه است.",
                reply_markup=Kb([[Btn("📊 جزئیات و ادامه", callback_data=f"j:{job_id}:view")]]))


# ---------------------------------------------------------------------------------------------------- chat events
async def admin_targets(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    db, _, cfg = deps(context)
    return set(cfg.admin_ids) | set(await db.admin_ids())


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ev = update.my_chat_member
    chat = ev.chat
    if chat.type not in CHAT_TYPES:
        return
    db, _, _ = deps(context)
    new = ev.new_chat_member
    member = new.status in MEMBER_STATES
    await db.upsert_chat(chat.id, chat.title or str(chat.id), chat.username, chat.type, can_post(chat.type, new), is_member=member)
    if member:
        post = can_post(chat.type, new)
        text = (f"✅ ربات در {ui.chat_label(chat.title, chat.username)} {'ادمین شد' if new.status == S.ADMINISTRATOR else 'عضو شد'}.\n"
                + ("می‌تواند هم مبدا باشد هم مقصد." if post else "فعلاً فقط به‌عنوان مبدا قابل استفاده است؛ برای مقصد دسترسی «ارسال پیام» لازم است."))
    else:
        text = f"⚠️ ربات از {ui.chat_label(chat.title, chat.username)} حذف شد."
    for uid in await admin_targets(context):
        try:
            await context.bot.send_message(uid, text, reply_markup=Kb([[Btn("➕ انتقال جدید", callback_data="m:new")]]) if member else None)
        except TelegramError:
            pass


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg or msg.pinned_message or msg.new_chat_title or msg.new_chat_photo or msg.delete_chat_photo or msg.channel_chat_created:
        return
    db, engine, _ = deps(context)
    for job in await db.mirror_jobs(msg.chat_id):
        await db.queue_live(job["id"], msg.message_id, msg.media_group_id)
        if job["status"] == "done":
            engine.schedule_drain(job["id"])


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("handler error", exc_info=context.error)


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("jobs", cmd_jobs, filters=private))
    app.add_handler(CommandHandler("chats", cmd_chats, filters=private))
    app.add_handler(CommandHandler("help", cmd_help, filters=private))
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(on_wizard, pattern=r"^w:"))
    app.add_handler(CallbackQueryHandler(on_job, pattern=r"^j:\d+:\w+$"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)


COMMANDS = [BotCommand("start", "منوی اصلی"), BotCommand("jobs", "انتقال‌ها و پیشرفت"),
            BotCommand("chats", "کانال‌های متصل"), BotCommand("help", "راهنما")]
