# Telegram Spam Filter Bot

A Python bot for Telegram groups. It counts each member's messages containing a
link or a photo and removes matching messages once that member's daily allowance
is exceeded. Group owners and administrators are exempt.

## Setup

1. Create a bot with Telegram's [@BotFather](https://t.me/BotFather).
2. Save its token as the `TELEGRAM_BOT_TOKEN` Replit Secret.
3. Add the bot to the group as an administrator and allow it to delete messages.
   Admin status lets Telegram deliver ordinary group messages to the bot.
4. Start the **Telegram Spam Filter Bot** workflow.

One link/photo message per member per group is allowed by default each day.
Further matching messages are deleted silently. `/start` and `/help` only reply
in private chats, never in the group.

## Choose a group's daily allowance

1. Open the bot in a private chat and send `/start`.
2. In each group you manage, send `/limit` once. This registers the group without
   making the bot post a reply there.
3. In the private chat with the bot, send `/limit`, choose a group, then choose
   an allowance. The menu offers 0, 1, 2, 3, 5, or 10 messages, or a custom value
   from 0 to 1000.

Only group administrators can change that group's allowance. Each limit applies
per member, per group, per day. A group's saved setting is stored in SQLite;
groups without an override use `DAILY_SPAM_LIMIT`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Required | Token created through BotFather; store only as a Replit Secret |
| `DAILY_SPAM_LIMIT` | `1` | Number of link/photo messages allowed per member, per group, per day |
| `BOT_TIMEZONE` | `Europe/Rome` | Timezone used to determine when the daily count resets |
| `SPAM_DB_PATH` | `telegram_spam.sqlite3` | SQLite file used to preserve counts across restarts |
| `LOG_LEVEL` | `INFO` | Python logging level |

Set `DAILY_SPAM_LIMIT=0` to remove every link/photo message from non-admins.
Counts are separated by group and member. The database stores Telegram IDs,
dates, and counts, not message text or photo contents; records older than 45
days are cleaned up as new messages arrive.

## Run and test

```sh
python main.py
python -m unittest discover -s telegram_spam_bot/tests
```

The bot uses long polling and drops messages queued while it was offline, so it
does not retroactively moderate old messages after a restart.