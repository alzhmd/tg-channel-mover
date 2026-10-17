"""Persian texts, progress rendering and keyboards."""
from __future__ import annotations

import html
import json
import time

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(value) -> str:
    """Number (or text) with Persian digits and Persian thousands separators."""
    if isinstance(value, int):
        value = f"{value:,}".replace(",", "٬")
    return str(value).translate(_FA)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def bar(frac: float | None, width: int = 16) -> str:
    if frac is None:
        return "░" * width
    full = int(max(0.0, min(1.0, frac)) * width)
    return "▓" * full + "░" * (width - full)


def duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{fa(max(sec, 1))} ثانیه"
    minutes = sec // 60
    if minutes < 60:
        return f"{fa(minutes)} دقیقه"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{fa(hours)} ساعت و {fa(minutes)} دقیقه" if minutes else f"{fa(hours)} ساعت"
    return f"{fa(hours // 24)} روز و {fa(hours % 24)} ساعت"


def chat_label(title, username=None) -> str:
    return f"<b>{esc(title)}</b>" + (f" (@{esc(username)})" if username else "")


def post_link(chat_id: int, username: str | None, msg_id: int | None) -> str:
    if msg_id is None:
        return ""
    if username:
        return f"https://t.me/{username}/{msg_id}"
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{msg_id}"


STATUS = {
    "new": "🆕 آماده", "scanning": "🔎 در حال شناسایی پست‌ها", "running": "🚚 در حال کپی", "paused": "⏸ متوقف",
    "done": "✅ تمام شد", "cancelled": "⛔ لغو شد", "error": "❌ خطا",
}

MAIN_TEXT = (
    "👋 سلام! من <b>انتقال‌دهنده کانال</b> هستم.\n\n"
    "کل محتوای یک کانال (از اولین تا آخرین پست) را در کانال دیگری کپی می‌کنم؛ <b>بدون نام فرستنده و بدون "
    "«فوروارد از …»</b>، با همان کپشن، فرمت و آلبوم‌ها. پست‌های جدیدِ مبدا را هم می‌توانم زنده منتقل کنم.\n\n"
    "برای شروع، ربات را <b>ادمین</b> هر دو کانال کنید (مبدا: هر دسترسی · مقصد: «ارسال پیام»)، "
    "بعد «➕ انتقال جدید» را بزنید."
)

HELP_TEXT = (
    "ℹ️ <b>راهنما</b>\n\n"
    "۱) ربات را ادمین کانال <b>مبدا</b> و کانال <b>مقصد</b> کنید. مقصد باید دسترسی «ارسال پیام» داشته باشد. "
    "کانال‌ها خودکار در «📡 کانال‌های متصل» ظاهر می‌شوند.\n"
    "۲) «➕ انتقال جدید» ← مبدا ← مقصد ← تنظیمات ← شروع.\n"
    "۳) پیشرفت از ۰ تا ۱۰۰٪ همین‌جا زنده نمایش داده می‌شود؛ می‌توانید متوقف/ادامه/لغو کنید. اگر ربات یا "
    "سرور ری‌استارت شود، کار از همان‌جا ادامه پیدا می‌کند.\n\n"
    "<b>نکات</b>\n"
    "• پست‌ها از قدیمی به جدید و با ترتیب اصلی ارسال می‌شوند؛ تاریخ پست‌ها در مقصد، زمان کپی است.\n"
    "• برای جلوگیری از مزاحمت اعضا، پیش‌فرض ارسال <b>بی‌صدا</b> است؛ مقصد را تا پایان کار عمومی نکنید.\n"
    "• کانالِ مبدا نباید «محدودیت ذخیره/فوروارد» داشته باشد، وگرنه تلگرام اجازه‌ی کپی نمی‌دهد.\n"
    "• لینک‌های داخل متن پست‌ها (مثل t.me/مبدا/123) خودکار تغییر نمی‌کنند؛ جدول «قدیم ← جدید» را با "
    "«📤 خروجی» بگیرید.\n"
    "• همگام‌سازی زنده: پست‌های تازه‌ی مبدا بعد از پایان کپی تاریخچه، خودکار در مقصد گذاشته می‌شوند.\n"
    "• کانال مبدای عمومی خودکار اسکن می‌شود. برای مبدای خصوصی بهتر است «📍 آخرین پست» را مشخص کنید."
)


def explain_error(reason: str) -> str:
    low = (reason or "").lower()
    if any(h in low for h in ("not a member", "kicked", "chat not found", "rights", "forbidden", "forward not found")):
        return ("ربات از مبدا یا مقصد حذف شده یا دسترسی لازم را ندارد. بررسی کنید ربات هنوز ادمین هر دو کانال است "
                "و در مقصد دسترسی «ارسال پیام» دارد.")
    if "network" in low or "timed out" in low:
        return "ارتباط با تلگرام پایدار نیست؛ کمی بعد دوباره «ادامه» را بزنید."
    return "علت را در متن بالا ببینید و پس از برطرف کردن، «ادامه» را بزنید."


def job_text(job, stats: dict | None = None, now: float | None = None) -> str:
    stats = stats or {}
    now = now or time.time()
    total, pos, status = job["total"] or 0, job["pos"] or 0, job["status"]
    if status == "scanning":
        progress = f"🔎 تا اینجا {fa(total)} پست پیدا شد…"
        frac = None
    else:
        if job["mode"] == "range":
            span = total - (job["start_pos"] or 1) + 1
            frac = min(0.99, (pos - (job["start_pos"] or 1)) / span) if total and status != "done" else (1.0 if status == "done" else None)
        else:
            frac = (pos / total) if total else None
        if status == "done":
            frac = 1.0
        pct = f"{fa(int(frac * 100))}٪" if frac is not None else "—"
        progress = f"{bar(frac)}  <b>{pct}</b>"
    lines = [
        f"<b>انتقال #{fa(job['id'])}</b> — {STATUS.get(status, status)}",
        f"📤 مبدا: {chat_label(job['src_title'], job['src_username'])}",
        f"📥 مقصد: {chat_label(job['dst_title'])}",
        "",
        progress,
    ]
    if status != "scanning":
        lines.append(f"✅ کپی‌شده: <b>{fa(job['copied'])}</b> پست")
        if job["skipped"]:
            lines.append(f"⏭ ردشده: {fa(job['skipped'])} شناسه بدون پستِ قابل‌کپی (پاک‌شده، پیام سرویس، …)")
        if job["failed"]:
            lines.append(f"⚠️ ناموفق: <b>{fa(job['failed'])}</b>")
    per_min = stats.get("per_min")
    if status == "running" and per_min:
        lines.append(f"⚡ سرعت: {fa(int(per_min))} پست در دقیقه")
        remaining = total - pos if total > pos else 0
        if remaining > 0:
            lines.append(f"⏳ زمان باقی‌مانده: ≈ {duration(remaining / per_min * 60)}")
    if status == "running" and stats.get("flood_until", 0) > time.monotonic():
        lines.append(f"🐢 تلگرام سرعت را محدود کرده؛ ادامه تا {fa(int(stats['flood_until'] - time.monotonic()))} ثانیه‌ی دیگر")
    rng = json.loads(job["opts"] or "{}")
    if rng.get("from_id") or rng.get("to_id"):
        lines.append(f"📍 بازه: از پست {fa(rng['from_id']) if rng.get('from_id') else 'اول'} تا "
                     f"{fa(rng['to_id']) if rng.get('to_id') else 'آخر'}")
    if job["mirror"]:
        lines.append("🔄 همگام‌سازی زنده: روشن" + (f" · {fa(job['live'])} پست جدید منتقل شد" if job["live"] else ""))
    extra = json.loads(job["opts"] or "{}")
    lines.append("🌟 موتور: اکانت پریمیوم" if extra.get("backend") == "user" else "🤖 موتور: ربات")
    if extra.get("upgraded"):
        lines.append(f"✨ {fa(extra['upgraded'])} پست با ایموجی پریمیوم بازنویسی شد.")
    if extra.get("buttons_lost"):
        lines.append(f"⚠️ دکمه‌ی {fa(extra['buttons_lost'])} پست منتقل نشد (ربات به مبدا دسترسی ندارد).")
    if extra.get("buttons_seen"):
        lines.append(f"🔘 دکمه‌های شیشه‌ایِ {fa(extra['buttons_seen'])} پست همراه خودِ پست منتقل می‌شود.")
    if extra.get("buttons_dropped"):
        lines.append(f"ℹ️ {fa(extra['buttons_dropped'])} دکمه از نوع «کالبک/ربات» است و بیرون از ربات اصلی کار نمی‌کند؛ منتقل نمی‌شود.")
    if status == "error" and job["error"]:
        lines.append(f"\n❌ <code>{esc(job['error'][:200])}</code>\n{explain_error(job['error'])}")
    return "\n".join(lines)


def job_keyboard(job, active: bool) -> Kb:
    j, s = job["id"], job["status"]
    rows: list[list[Btn]] = []
    if s in ("running", "scanning") and active:
        rows.append([Btn("⏸ توقف", callback_data=f"j:{j}:pause"), Btn("⛔ لغو", callback_data=f"j:{j}:cancel")])
    elif s in ("paused", "error"):
        rows.append([Btn("▶️ ادامه", callback_data=f"j:{j}:resume"), Btn("⛔ لغو", callback_data=f"j:{j}:cancel")])
    elif s in ("running", "scanning"):  # task not alive (e.g. after a crash)
        rows.append([Btn("▶️ ادامه", callback_data=f"j:{j}:resume")])
    extra = [Btn("🔄 تازه‌سازی", callback_data=f"j:{j}:view")]
    if s in ("done", "cancelled", "error", "paused", "running") and (job["copied"] or job["live"]):
        extra.append(Btn("📤 خروجی قدیم←جدید", callback_data=f"j:{j}:export"))
    rows.append(extra)
    if job["skipped"]:
        rows.append([Btn(f"⏭ ردشده‌ها چیست؟ ({fa(job['skipped'])})", callback_data=f"j:{j}:skipped")])
    if job["failed"] and s in ("done", "cancelled", "error"):
        rows.append([Btn(f"🔁 تلاش دوباره ({fa(job['failed'])} ناموفق)", callback_data=f"j:{j}:retry"),
                     Btn("📄 فهرست ناموفق‌ها", callback_data=f"j:{j}:fails")])
    elif job["failed"]:
        rows.append([Btn("📄 فهرست ناموفق‌ها", callback_data=f"j:{j}:fails")])
    if s in ("done", "paused", "cancelled", "error") and job["copied"] and not active:
        rows.append([Btn("🧹 پاک کردن پست‌های این انتقال از مقصد", callback_data=f"j:{j}:undo")])
    if s in ("done", "paused", "running"):
        rows.append([Btn("🔄 همگام‌سازی زنده: " + ("خاموش کن" if job["mirror"] else "روشن کن"), callback_data=f"j:{j}:mirror")])
    rows.append([Btn("🏠 منوی اصلی", callback_data="m:home")])
    return Kb(rows)


def main_keyboard() -> Kb:
    return Kb([
        [Btn("➕ انتقال جدید", callback_data="m:new")],
        [Btn("📋 انتقال‌ها", callback_data="m:jobs"), Btn("📡 کانال‌های متصل", callback_data="m:chats")],
        [Btn("🌟 اکانت پریمیوم", callback_data="m:premium"), Btn("ℹ️ راهنما", callback_data="m:help")],
    ])


def back_home() -> Kb:
    return Kb([[Btn("🏠 منوی اصلی", callback_data="m:home")]])
