# Studio staff Telegram bot

[Русский](TELEGRAM.md) · **English**

The optional bot shows bookings in private chats to allowlisted Telegram
accounts only. Groups/channels are unsupported. Bot replies remain Russian;
website language is independent. This guide does not change the bot's interface.

## Features and boundary

Notifications cover holds, demo-payment success/failure, cancellations, full
demo refunds, and expiry. Cards include full booking ID, service, employee,
customer, Moscow time, price, and payment state. Commands provide date-based
schedules, pagination, booking lookup, totals, and help. A persisted queue
supports retries and a delivery log in protected administration.

The bot reads the website's PostgreSQL database. Telegram commands cannot
confirm payments or change bookings. Financial summaries describe simulated payments.

## 1. Create the bot

Use `/newbot` with the [official BotFather](https://t.me/BotFather), choose a
display name and an available username ending in `bot`. Keep its token private;
edit the local `.env`, never a command line or public screenshot:

```dotenv
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_IDS=
```

Set TELEGRAM_BOT_TOKEN. While disabled, the site creates no notifications.
The ignored `.env` should be owner-only (mode 600); `.env.example` has no live keys.

## 2. Identify authorized accounts

Use the existing environment from the repository root:

```bash
bash scripts/run_telegram.sh --identify
```

Expected: `Telegram bot ready (identity only).` Open your bot and send `/start`
or `/whoami`. This mode reveals only the sender's own numeric ID, with no
booking access. No third-party identity bot is required.

Set TELEGRAM_ENABLED=true and place that numeric ID in TELEGRAM_ADMIN_IDS.
Separate multiple trusted owners with commas. Each receives access to all
studio bookings and must first start a private chat. Do not add customer IDs.

## 3. Start normal operation

Stop identification with Ctrl+C, then:

```bash
bash scripts/run_telegram.sh
```

Expected: `Telegram staff bot ready. Ctrl+C stops it.` Restart the website too,
so both processes load the new `.env`. [Website setup](SETUP.md).
Keep both terminals running. Outgoing HTTPS polling needs no public website URL
and installs no packages. Continuous operation needs a supervised server later.

## Commands and live checks

| Command | Result |
| --- | --- |
| `/start`, `/help` | Menu and help |
| `/today` | Today's confirmed bookings and active holds |
| `/tomorrow` | Tomorrow's schedule |
| `/upcoming` | Through the end of day seven, including today |
| `/date 2026-11-02` | Selected date; example format |
| `/today 2` | Page two, five bookings per page |
| `/date 2026-11-02 2` | Page two of a selected date |
| `/booking UUID` | Full booking card; replace UUID with the complete ID |
| `/stats` | Today's counts/confirmed amounts and queue state |
| `/whoami` | Sender's numeric Telegram ID |

Create a synthetic website booking, complete demo payment, then cancel at least
two hours before the visit. Verify matching notifications/refund states.
Supported website retries do not enqueue the same event again. Times are Moscow,
amounts RUB; names/prices come from saved snapshots. Schedules exclude cancelled
and expired holds; lookup by complete ID retains payment history.

## Delivery semantics

The business event and delivery row share a database transaction. Website
requests never wait for Telegram. A separate process sends the saved snapshot
and records the result; a delayed hold notification may arrive after confirmation.
Use `/booking` for current state.

One queue row exists per event/booking/bot/recipient. Successful rows are not
normally resent. Temporary errors retry up to five attempts with backoff;
Telegram retry_after pauses the whole bot. Invalid requests/tokens/blocked users
mark a delivery Failed. Only sanitized descriptions/numeric codes are logged.

Ambiguous network failures or a crash after Telegram acceptance but before
recording success may duplicate a message. sendMessage has no application
idempotency key; exactly-once delivery is not claimed. Booking state is unaffected.

In admin, **Telegram deliveries** shows text/state/attempts/next time. Fix the
cause, then use **Retry failed Telegram deliveries** on Failed rows. Snapshots
contain customer data: protect databases, backups, and Telegram accounts.
Automatic notification-history retention is not configured.

## Access and process recovery

Only private messages with matching sender/chat IDs and allowlisted numeric
accounts can query data. Change the list and restart both processes to revoke;
unsent messages for removed accounts become Revoked. Commands are limited to
20/minute/account. Replies use plain text with link previews disabled and normal
forwarding/saving protection; this does not prevent screenshots or unlocked-phone access.

Update offsets persist per bot. A crash after replying but before saving an
offset can repeat a read-only reply. A PostgreSQL advisory lock permits one
worker per database. An existing webhook blocks startup rather than being
silently removed. The running worker reconciles expired holds every minute.

Only events after enabling notifications are queued. Existing records remain
readable. A stopped process accumulates work; TELEGRAM_ENABLED=false stops new
queue creation. Rotating the same bot's token keeps its queue; a different bot
does not inherit the old bot's queue/offsets.

## Possible next features

Client reminders, morning staff schedules, per-employee views, a waiting list,
repeat-booking links, and authorized rescheduling/cancellation require separate
account linking and consent/confirmation rules. These are proposals, not shipped features.

Official references: [bot creation](https://core.telegram.org/bots/features#creating-a-new-bot),
[getUpdates](https://core.telegram.org/bots/api#getupdates),
[sendMessage](https://core.telegram.org/bots/api#sendmessage),
[response parameters](https://core.telegram.org/bots/api#responseparameters).
