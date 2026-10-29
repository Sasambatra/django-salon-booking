"""One foreground poller; supervisable separately from the web server."""
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from booking.models import TelegramCursor
from booking.services import expire_due_holds
from booking.telegram import COMMANDS, TelegramClient, TelegramError, deliver_pending, handle_update


LOCK_ID = 721947102


class Command(BaseCommand):
    help = "Run the private Telegram staff bot. No packages are installed."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
        parser.add_argument("--identify", action="store_true", help="Only return the sender's own Telegram ID; no booking data.")

    def handle(self, **options):
        identify = options["identify"]
        if not identify and (not settings.TELEGRAM_ENABLED or not settings.TELEGRAM_ADMIN_IDS):
            raise CommandError("Set TELEGRAM_ENABLED=true and TELEGRAM_ADMIN_IDS, or use --identify first.")
        try:
            client = TelegramClient(settings.TELEGRAM_BOT_TOKEN)
        except ImproperlyConfigured as error:
            raise CommandError(str(error)) from None
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            locked = cursor.fetchone()[0]
        if not locked:
            raise CommandError("Another Telegram worker is running for this database. Stop it first.")
        try:
            self.run(client, identify, options["once"])
        except TelegramError as error:
            raise CommandError(str(error)) from None
        except KeyboardInterrupt:
            self.stdout.write("Telegram bot stopped.")
        finally:
            if connection.connection is not None:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    def run(self, client, identify, once):
        info = client.call("getMe")
        if info.get("id") != client.bot_id or info.get("is_bot") is not True:
            raise CommandError("Telegram returned an unexpected bot identity.")
        if client.call("getWebhookInfo").get("url"):
            raise CommandError("This bot has an active webhook. Use a dedicated bot or deliberately remove its webhook first.")
        if not identify:
            client.call("setMyCommands", commands=[{"command": name, "description": description} for name, description in COMMANDS])
        state, _ = TelegramCursor.objects.get_or_create(bot_id=client.bot_id)
        self.stdout.write("Telegram bot ready (identity only)." if identify else "Telegram staff bot ready. Ctrl+C stops it.")
        next_expiry_check = 0
        while True:
            try:
                if not identify:
                    if time.monotonic() >= next_expiry_check:
                        expire_due_holds()
                        next_expiry_check = time.monotonic() + 60
                    counts = deliver_pending(client)
                    if any(counts.values()):
                        self.stdout.write("Delivery batch: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
                updates = client.call(
                    "getUpdates", offset=state.next_update_id, limit=20, allowed_updates=["message"],
                    timeout=0 if once else 10,
                )
                for update in updates:
                    update_id = update.get("update_id")
                    if type(update_id) is not int or update_id < state.next_update_id:
                        continue
                    try:
                        handle_update(client, update, identify_only=identify)
                    except TelegramError as error:
                        if error.code not in {400, 403}:
                            raise
                        self.stderr.write(str(error))
                    state.next_update_id = update_id + 1
                    state.save(update_fields=["next_update_id"])
            except TelegramError as error:
                if once or error.code in {401, 404, 409}:
                    raise
                self.stderr.write(str(error))
                remaining = max(5, error.retry_after)
                while remaining > 0:
                    pause = min(30, remaining)
                    time.sleep(pause)
                    remaining -= pause
            if once:
                return
