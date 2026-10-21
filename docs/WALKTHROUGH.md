# Walkthrough

The bot talks Persian; here is the flow with the texts translated.

## 1. Link the Premium account (once)

```
🌟 Premium account
   Bots (even Premium-owned ones) cannot place Premium emoji in a channel - Telegram forbids it. To keep them,
   the transfer must be done by a Premium account that administers both channels. Linking works like
   "Link Desktop Device" with a QR code; you can disconnect any time.            [🔗 Connect with QR]

📲 In Telegram on your phone: Settings → Devices → Link Desktop Device, scan this code.
   Or, if you are on this phone, tap this link.                        (the code refreshes every 30 s, 5 min in total)

✅ <Name> is connected. The account is Premium; emoji are transferred completely.        [➕ New transfer]
```
If the account has two-step verification the bot asks for the password and deletes the message at once.

## 2. New transfer

```
1️⃣ Choose the SOURCE channel          [Old channel] [Another channel] [🔎 by @name / link / invite / forwarded post]
2️⃣ Choose the DESTINATION channel     [Archive] [...]

3️⃣ Transfer settings
   📤 Source: Old channel   📥 Destination: Archive
   🌟 Premium-account engine: special emoji are kept and ordinary emoji become Premium. Sender name and
      "Forwarded from" are removed.
   [Engine: 🌟 Premium account (change)]
   [📍 From post: first post] [🏁 To post: last post]
   [🔕 Silent: on ✅] [🔄 Live mirror: on ✅] [✨ Ordinary → Premium emoji: on ✅]
   [🔗 Links in posts → this channel (while copying): on ✅] [🏷 Former usernames of the source: OldName]
   [🚫 Remove captions] [🚀 Continue] [✖️ Cancel]

❓ Sure?  All posts of «Old channel» will be sent to «Archive». This cannot be undone.
   Tip: keep the destination private until the end.                         [✅ Yes, start] [↩️ Back]
```

## 3. Progress (updates itself)

```
Transfer #7 - 🚚 copying
📤 Source: Old channel (@old)   📥 Destination: Archive

▓▓▓▓▓▓▓▓░░░░░░░░  52%
✅ Copied: 7,300 posts
⏭ Skipped: 1 id without a copyable post (deleted, service message, ...)
⚡ Speed: 64 posts per minute   ⏳ Remaining: ≈ 1 hour 45 minutes
📍 Range: from post 100 to 5,000                (only if a range is set)
🔄 Live mirror: on   🌟 Engine: Premium account
✨ 3,800 posts rewritten (Premium emoji / links)
[⏸ Pause] [⛔ Cancel]  [🔄 Refresh] [📤 Export old→new]  [⏭ What is "skipped"?]  [🧹 Delete what this transfer created]
```
A flood wait shows as "🐢 Telegram slowed the speed; continuing in N seconds". Waits of 5+ minutes also arrive as a message.

## 4. When it ends

```
✅ Transfer complete - #7
✅ Copied: 14,068   ⏭ Skipped: 1   ⚠️ Failed: 0   ✨ Rewritten with Premium emoji / links: 7,290
🔄 Live mirror is on; new posts of the source are transferred automatically.
[📊 Details] [📤 Export old→new]
```
