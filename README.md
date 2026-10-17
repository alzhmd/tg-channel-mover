# Channel Mover — انتقال‌دهنده کانال تلگرام

[فارسی](#فارسی) · [English](#english)

---

## فارسی

رباتی برای **کپی کردن کل محتوای یک کانال تلگرام در کانال دیگر** — از اولین تا آخرین پست — **بدون نام فرستنده و بدون «فوروارد از …»**،
با همان متن، کپشن، فرمت، فایل‌ها (حتی بالای ۲۰ مگابایت)، نظرسنجی‌ها و آلبوم‌ها. وقتی کانال مبدا قرار است حذف شود و باید فعالیت
در کانال جدید ادامه پیدا کند، یا هر بار که لازم باشد این کار تکرار شود.

### امکانات

- منوی کامل فارسی با دکمه: انتخاب مبدا و مقصد، تنظیمات، شروع، **توقف / ادامه / لغو** و نوار پیشرفت زنده (۰ تا ۱۰۰٪، سرعت، زمان باقی‌مانده)
- کپی سمت سرور تلگرام (`copyMessages`)؛ فایلی دانلود یا آپلود نمی‌شود، پس سریع است و محدودیت ۲۰ مگابایتی ربات‌ها ندارد
- ترتیب اصلی پست‌ها حفظ می‌شود و آلبوم‌ها کنار هم می‌مانند
- **دکمه‌های شیشه‌ای لینک‌دار** (مثل «تماشا» که به ربات یا سایت می‌رود) همراه پست منتقل می‌شوند؛ هم برای تاریخچه، هم برای پست‌های جدید
- **ادامه‌ی خودکار** بعد از ری‌استارت ربات یا سرور؛ مدیریت خودکار محدودیت سرعت تلگرام (flood wait)
- **همگام‌سازی زنده**: پست‌های جدیدِ مبدا بعد از پایان کپی تاریخچه، خودکار به مقصد می‌روند
- خروجی CSV «شناسه‌ی قدیم ← شناسه‌ی جدید» برای اصلاح لینک‌ها و فهرست‌های قدیمی
- کپی چند کانال، به هر تعداد و هر زمان؛ هر انتقال یک کار مستقل است
- فقط مدیرهای مجاز می‌توانند از ربات استفاده کنند

### دو موتور، یک ربات

| | 🤖 موتور ربات | 🌟 موتور اکانت پریمیوم |
|---|---|---|
| چطور کار می‌کند | Bot API (`copyMessages`) | اکانت پریمیومِ شما (MTProto) با اتصال QR |
| ایموجی پریمیوم | **از بین می‌رود** (تلگرام به ربات‌ها در کانال اجازه نمی‌دهد) | حفظ می‌شود + ایموجی‌های معمولی **خودکار پریمیوم** می‌شوند |
| سرعت | حدود ۶۰ پست در دقیقه | چند برابر سریع‌تر (فوروارد بدون نام ۱۰۰تایی) |
| لازم دارد | ربات ادمین مبدا و مقصد | اکانت پریمیوم: عضو مبدا، ادمین مقصد؛ `TG_API_ID`/`TG_API_HASH` |
| نگاشت قدیم←جدید | گاهی فقط بازه‌ای | همیشه دقیق |

> **چرا ربات نمی‌تواند؟** طبق تغییرات رسمی Bot API (نسخه ۹٫۴) ربات‌هایی که مالکشان پریمیوم است فقط در چت خصوصی، گروه و سوپرگروه
> می‌توانند ایموجی پریمیوم بفرستند، **کانال** شامل نیست. تست واقعی هم همین را نشان داد: حتی با توکنِ ساخته‌شده توسط اکانت پریمیوم،
> ارسال و کپی ربات در کانال ایموجی ویژه را حذف می‌کند. فقط یک **کاربر پریمیوم** می‌تواند آن را در کانال بگذارد.

ربات هوشمند است: «🌟 اکانت پریمیوم» را از منوی اصلی وصل کنید (QR یا لینک؛ اگر حساب رمز دومرحله‌ای دارد، رمز را می‌فرستید و پیامش فوراً پاک
می‌شود). اگر اکانت Premium باشد، موتور به‌طور پیش‌فرض روی «اکانت پریمیوم» می‌ماند؛ در غیر این صورت ربات هشدار می‌دهد که ایموجی‌های ویژه ساده
می‌شوند. دکمه‌ی «🔎 بررسی ایموجی ربات» هم با یک پیام آزمایشی در همان چت می‌گوید مالکِ توکن Premium است یا نه.

نکته‌های موتور پریمیوم: ایموجی‌های بدون «همتای پریمیوم» (مثل 🎥) تا وقتی «جایگزین نزدیک» روشن نباشد ساده می‌مانند؛ نگاشت ایموجی‌ها
در `chanmover/data/premium_emoji.json` است (`{"ids": {"😀": "<custom emoji id>"}, "aliases": {...}}`) و با `PREMIUM_EMOJI_FILE` عوض می‌شود.
**سرعت ایمن:** اکانت شما حدود یک پست در هر ۰٫۸ ثانیه می‌نویسد و با هر `FLOOD_WAIT` تلگرام خودکار کندتر می‌شود؛ انتظارها دقیقاً رعایت می‌شوند (حتی بعد از ری‌استارت) و انتظار بلند به خودِ شما اطلاع داده می‌شود. تلگرام سقف‌ها را منتشر نمی‌کند و بالا زدن سرعت فقط انتظارها را بلندتر می‌کند؛ کانال‌های بزرگ چند ساعت طول می‌کشد.
پست‌های دکمه‌دار با کمک ربات منتقل می‌شوند (کاربر نمی‌تواند دکمه‌ی شیشه‌ای بفرستد)، پس ایموجی ویژه‌شان ساده می‌ماند؛ برای همگام‌سازی زنده
و دکمه‌ها ربات باید در مبدا هم ادمین باشد. گزینه‌ی «محافظت از محتوا» فقط در موتور ربات است.

### نصب

```bash
git clone https://github.com/alzhmd/tg-channel-mover.git && cd tg-channel-mover
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env     # BOT_TOKEN و ADMIN_IDS را پر کنید
./venv/bin/python -m chanmover
```

برای اجرای دائمی با systemd فایل `deploy/channel-mover.service` را (با مسیر خودتان) در `/etc/systemd/system/` بگذارید و
`systemctl enable --now channel-mover` بزنید.

### روش استفاده

1. در [@BotFather](https://t.me/BotFather) یک ربات بسازید و توکنش را در `.env` بگذارید. شناسه‌ی عددی تلگرام خودتان را در `ADMIN_IDS` بنویسید.
2. ربات را **ادمین کانال مبدا** (هر دسترسی) و **ادمین کانال مقصد** (دسترسی «ارسال پیام») کنید. ربات در پیوی خبر می‌دهد.
3. در ربات: «➕ انتقال جدید» ← مبدا ← مقصد ← تنظیمات ← شروع.

### نکته‌ها و محدودیت‌ها

- **سرعت را تلگرام تعیین می‌کند.** ربات‌ها در یک کانال هم‌زمان فقط چند ده پست در دقیقه می‌توانند بفرستند و ربات هنگام دریافت `retry_after`
  صبر می‌کند. برای کانال‌های ده‌ها هزار پستی چند ساعت زمان لازم است؛ ربات خودش ادامه می‌دهد.
- تاریخ پست‌ها در مقصد، زمان کپی است (تلگرام اجازه‌ی تغییر نمی‌دهد). پیش‌فرض ارسال **بی‌صدا** است تا اعضا نوتیف نگیرند؛ مقصد را تا پایان کار عمومی نکنید.
- کانال مبدا نباید «محدودیت ذخیره و فوروارد» داشته باشد؛ تلگرام کپی چنین پست‌هایی را ممنوع می‌کند.
- لینک‌های داخل متن پست‌ها (مثل `t.me/مبدا/123`) خودکار عوض نمی‌شوند؛ از خروجی CSV استفاده کنید.
- تلگرام در کپی، دکمه‌های شیشه‌ای را حذف می‌کند؛ ربات آن‌ها را از پیش‌نمایش عمومیِ کانال می‌خواند و دوباره می‌گذارد. این فقط برای **کانال مبدای عمومی** ممکن است (برای مبدای خصوصی آن را موقتاً عمومی کنید)، و فقط دکمه‌های **لینکی**؛ دکمه‌های «کالبک» فقط داخل ربات سازنده‌شان کار می‌کنند و منتقل نمی‌شوند (تعدادشان در گزارش می‌آید).
- پیام‌های سرویس (پین، ساخت کانال) و نوع‌هایی که تلگرام کپی نمی‌کند «ردشده» شمرده می‌شوند؛ دکمه‌ی «ردشده‌ها چیست؟» در ربات توضیح و فهرست می‌دهد.
- ربات نمی‌تواند لینک دعوت خصوصی (`t.me/+…`) را باز کند؛ باید ادمین آن کانال باشد.
- اگر پروسه وسط کار با کشته شدن ناگهانی (kill -9 / قطع برق) قطع شود، ممکن است یک «پنجره»‌ی کوچک (حداکثر `BATCH_SIZE` پست) در مقصد تکراری شود؛ ری‌استارت معمولی بی‌خطر است.

### چطور کار می‌کند

ربات (Bot API) نمی‌تواند تاریخچه‌ی کانال را بخواند، ولی `copyMessages` را روی هر شناسه‌ی پست اجرا می‌کند. پس بازه‌ی شناسه‌ها را به
پنجره‌های کوچک می‌شکند و با همان ترتیب کپی می‌کند. برای کانال‌های عمومی، آخرین شناسه (و شناسه‌ی پست‌های دیده‌شده) از پیش‌نمایش
وب `t.me/s/<username>` خوانده می‌شود تا بازه‌ی دقیق و پیشرفت درست باشد؛ برای کانال خصوصی تا چند صد شناسه بعد از آخرین پست جلو می‌رود
(یا آخرین پست را برایش فوروارد کنید). نگاشت قدیم←جدید فقط وقتی ثبت می‌شود که یقین باشد؛ پنجره‌های مبهم جدا نگه داشته می‌شوند.

### پیکربندی (`.env`)

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `BOT_TOKEN` | — | توکن ربات |
| `ADMIN_IDS` | — | شناسه‌های عددی مدیران، با کاما |
| `CLAIM_CODE` | خالی | اگر پر باشد، هر کس `/start <کد>` بفرستد مدیر می‌شود |
| `DB_PATH` | `data/chanmover.db` | مسیر SQLite |
| `TG_API_ID` / `TG_API_HASH` | — | اطلاعات برنامه از my.telegram.org؛ فقط برای موتور اکانت پریمیوم |
| `USER_SESSION` | `data/user` | فایل نشست اکانت وصل‌شده |
| `PREMIUM_EMOJI_FILE` | خالی | نگاشت ایموجی معمولی←پریمیوم (خالی: نسخه‌ی همراه) |
| `USER_BATCH_SIZE` | `20` | تعداد پست در هر پنجره‌ی موتور پریمیوم |
| `USER_MIN_INTERVAL` | `0.8` | ثانیه‌ی انتظار برای هر پستی که اکانت می‌نویسد (سرعت ایمن؛ بعد از هر محدودیت تلگرام خودکار کندتر می‌شود) |
| `BATCH_SIZE` | `20` | تعداد پست در هر درخواست (حداکثر ۱۰۰)؛ بعد از flood wait خودکار کم می‌شود |
| `BATCH_DELAY` | `1.0` | مکث بین درخواست‌ها (ثانیه) |
| `LOOKAHEAD` | `600` | کانال خصوصی: چند شناسه‌ی خالی بعد از آخرین پست دیده شود |
| `SCRAPE_DELAY` | `0.35` | مکث بین صفحه‌های پیش‌نمایش (ثانیه) |

### توسعه

```bash
./venv/bin/pip install pytest pytest-asyncio && ./venv/bin/python -m pytest
```

---

## English

A Telegram bot that **copies a whole channel into another one** — first post to last — **without the sender name or a
"Forwarded from" header**, keeping text, captions, formatting, files (even above the 20 MB bot download limit), polls
and albums. Built for the moment a source channel is about to disappear and the audience moves to a new one, and for
repeating that as often as needed.

**Features:** URL inline buttons are carried over (read from the public preview, or from the update for live posts) · Persian button UI (pick source/destination, options, start, pause/resume/cancel, live 0–100 % progress with
speed and ETA) · server-side `copyMessages` (nothing is downloaded or uploaded) · original order and albums preserved ·
automatic resume after restarts and automatic flood-wait handling · live mirroring of new source posts once the history
is done · CSV export of the old→new message id map · any number of independent jobs · admin-only access.

**Two engines.** *Bot engine*: Bot API `copyMessages` — Telegram strips Premium emoji from anything a bot sends or copies into a
channel (Bot API 9.4 only allows them in private chats and groups; verified with a Premium-owned bot). *Premium-account engine*: the
copying is done by your Premium Telegram account (linked with a QR code, like Telegram Desktop) — Premium emoji are kept, ordinary
emoji are upgraded to Premium emoji automatically (map in `chanmover/data/premium_emoji.json`), sender is hidden, albums/media are
reused by reference, posts needing no change are forwarded server-side without the author (100 per call), and the old→new id map is
always exact. Set `TG_API_ID`/`TG_API_HASH` to enable it; the bot picks it by default when the linked account is Premium.

**Pace.** The account writes about one post per 0.8 s (`USER_MIN_INTERVAL`), slows down further after every `FLOOD_WAIT`, waits out each one exactly (also across restarts) and tells you about long ones. Telegram publishes no limits and pushing harder only lengthens the waits, so a big channel takes hours.

**Setup:** `pip install -r requirements.txt`, copy `.env.example` to `.env` (set `BOT_TOKEN`, `ADMIN_IDS`), run
`python -m chanmover`, add the bot as an admin of both channels (source: any rights, destination: *Post messages*),
then use *New transfer* in the bot. A systemd unit is in `deploy/`.

**Good to know**

- Throughput is set by Telegram's per-chat flood limits, not by the bot — expect on the order of tens of posts per
  minute; very large channels take hours. The bot waits on `retry_after` by itself and resumes after restarts.
- Posts get the copy time as their date. Sending is silent by default; keep the destination private until the job is done.
- The source must not have *Restrict saving content* enabled. Service messages and types Telegram refuses to copy are
  reported as skipped. Links inside post texts are not rewritten — use the CSV export. Telegram strips inline buttons when copying, so the bot re-attaches *URL* buttons it reads from the channel's public preview (public sources only; callback buttons only work inside their own bot and are reported, not copied).
- The Bot API cannot read channel history, so ids are walked in order with `copyMessages`; for public channels the
  `t.me/s/<username>` preview supplies the newest id and exact old→new pairs, for private ones the scan runs a
  configurable number of ids past the newest post found (or forward the last post to the bot).
- A hard kill (SIGKILL / power loss) in the middle of a window can duplicate at most `BATCH_SIZE` posts on resume; a
  normal `systemctl restart` finishes the window in flight first.

License: MIT
