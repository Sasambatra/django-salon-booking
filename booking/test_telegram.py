"""Telegram boundaries use fake transport; tests never contact Telegram."""
import io
import json
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from booking.models import Booking, TelegramCursor, TelegramDelivery
from booking.services import cancel, expire_employee_holds, reserve, salon_zone
from booking.telegram import (
    TelegramClient, TelegramError, booking_card, bot_id_from_token, command_reply,
    deliver_pending, handle_update, queue_booking_event,
)
from booking.test_workflow import BookingData, next_day


BOT_ID = 123456789
TOKEN = str(BOT_ID) + ":" + "x" * 35
STAFF_ID = 741852963
BOT_SETTINGS = dict(TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_ADMIN_IDS=(STAFF_ID,))


def message_update(text, *, user_id=STAFF_ID, chat_type="private", update_id=1):
    return {"update_id": update_id, "message": {
        "chat": {"id": user_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False}, "text": text,
    }}


@override_settings(**BOT_SETTINGS)
class TelegramBookingTests(BookingData, TestCase):
    def fake_client(self):
        client = Mock()
        client.bot_id = BOT_ID
        return client

    def test_booking_and_events_are_queued_once_with_status_snapshots(self):
        booking = self.make_hold()
        self.make_hold(request_key=booking.request_key)
        original = TelegramDelivery.objects.get(event="reserved").text
        self.assertIn("Ожидает демо-оплаты", original)
        self.pay(booking)
        self.pay(booking)
        confirmed = TelegramDelivery.objects.get(event="confirmed").text
        self.assertIn("Демо-оплата успешна", confirmed)
        cancel(booking_id=booking.pk, customer=self.user)
        cancel(booking_id=booking.pk, customer=self.user)
        self.assertEqual(TelegramDelivery.objects.count(), 3)
        self.assertIn("Полный демо-возврат выполнен", TelegramDelivery.objects.get(event="cancelled").text)
        self.assertEqual(TelegramDelivery.objects.get(event="reserved").text, original)

    @override_settings(TELEGRAM_ENABLED=False)
    def test_disabled_integration_does_not_queue(self):
        booking = self.pay(self.make_hold())
        cancel(booking_id=booking.pk, customer=self.user)
        self.assertFalse(TelegramDelivery.objects.exists())

    def test_outbox_rolls_back_with_the_booking(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.make_hold()
                raise RuntimeError("Roll back this booking")
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(TelegramDelivery.objects.exists())

    @patch("booking.telegram.TelegramClient")
    def test_booking_operation_never_contacts_telegram(self, transport):
        self.pay(self.make_hold())
        transport.assert_not_called()

    def test_failed_payment_has_an_explicit_notification(self):
        self.pay(self.make_hold(), outcome="failure")
        text = TelegramDelivery.objects.get(event="payment_failed").text
        self.assertIn("Демо-оплата не выполнена", text)
        self.assertIn("Отменена", text)

    def test_expiry_is_queued_once(self):
        booking = self.make_hold()
        Booking.objects.filter(pk=booking.pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        with transaction.atomic():
            expire_employee_holds(self.employee, timezone.now())
            expire_employee_holds(self.employee, timezone.now())
        self.assertEqual(TelegramDelivery.objects.filter(event="expired").count(), 1)
        self.assertIn("Время демо-оплаты истекло", TelegramDelivery.objects.get(event="expired").text)

    @override_settings(TELEGRAM_ADMIN_IDS=(STAFF_ID, STAFF_ID + 1))
    def test_each_approved_recipient_has_a_separate_delivery(self):
        booking = self.make_hold()
        queue_booking_event(booking, "reserved")
        self.assertEqual(TelegramDelivery.objects.count(), 2)

    def test_delivery_marks_sent_and_does_not_resend(self):
        self.make_hold()
        client = self.fake_client()
        result = deliver_pending(client)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(deliver_pending(client)["sent"], 0)
        self.assertEqual(client.send_message.call_count, 1)
        delivery = TelegramDelivery.objects.get()
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNotNone(delivery.sent_at)

    def test_transient_failure_is_retried_later_without_affecting_booking(self):
        booking = self.make_hold()
        client = self.fake_client()
        client.send_message.side_effect = TelegramError()
        now = timezone.now()
        self.assertEqual(deliver_pending(client, now=now)["retried"], 1)
        delivery = TelegramDelivery.objects.get()
        self.assertEqual(delivery.next_attempt_at, now + timedelta(seconds=15))
        self.assertEqual(deliver_pending(client, now=now)["retried"], 0)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HELD)
        client.send_message.side_effect = None
        self.assertEqual(deliver_pending(client, now=now + timedelta(seconds=16))["sent"], 1)

    def test_flood_limit_pauses_all_deliveries_for_this_bot(self):
        booking = self.make_hold()
        self.pay(booking)
        client = self.fake_client()
        client.send_message.side_effect = TelegramError(429, 120)
        now = timezone.now()
        deliver_pending(client, now=now)
        deliver_pending(client, now=now + timedelta(seconds=60))
        self.assertEqual(client.send_message.call_count, 1)
        self.assertEqual(TelegramCursor.objects.get().delivery_not_before, now + timedelta(seconds=120))

    def test_five_transient_attempts_stop_automatic_retries(self):
        self.make_hold()
        client = self.fake_client()
        client.send_message.side_effect = TelegramError(503)
        for _ in range(5):
            deliver_pending(client, now=TelegramDelivery.objects.get().next_attempt_at + timedelta(seconds=1))
        self.assertEqual(TelegramDelivery.objects.get().status, TelegramDelivery.Status.FAILED)
        self.assertEqual(TelegramDelivery.objects.get().attempts, 5)
        deliver_pending(client, now=timezone.now() + timedelta(days=1))
        self.assertEqual(client.send_message.call_count, 5)

    def test_blocked_bot_is_a_permanent_failure(self):
        self.make_hold()
        client = self.fake_client()
        client.send_message.side_effect = TelegramError(403)
        self.assertEqual(deliver_pending(client)["failed"], 1)
        self.assertEqual(TelegramDelivery.objects.get().last_error, "Telegram API error 403")

    @override_settings(TELEGRAM_ENABLED=False)
    def test_disabled_worker_does_not_deliver_existing_queue(self):
        with override_settings(TELEGRAM_ENABLED=True):
            self.make_hold()
        client = self.fake_client()
        deliver_pending(client)
        client.send_message.assert_not_called()

    def test_removed_recipient_is_not_sent_private_data(self):
        self.make_hold()
        client = self.fake_client()
        with override_settings(TELEGRAM_ADMIN_IDS=()):
            self.assertEqual(deliver_pending(client)["revoked"], 1)
        client.send_message.assert_not_called()

    def test_different_bot_cannot_deliver_old_bots_queue(self):
        self.make_hold()
        client = self.fake_client()
        client.bot_id += 1
        deliver_pending(client)
        client.send_message.assert_not_called()

    def test_unapproved_user_cannot_read_booking_or_schedule(self):
        booking = self.make_hold()
        client = self.fake_client()
        for command in ("/today", "/stats", f"/booking {booking.pk}"):
            handle_update(client, message_update(command, user_id=999))
        client.send_message.assert_not_called()

    def test_group_and_mismatched_sender_are_ignored(self):
        client = self.fake_client()
        handle_update(client, message_update("/today", chat_type="group"))
        update = message_update("/today")
        update["message"]["chat"]["id"] = 999
        handle_update(client, update)
        client.send_message.assert_not_called()

    def test_whoami_only_reveals_senders_own_id(self):
        self.make_hold()
        client = self.fake_client()
        handle_update(client, message_update("/whoami", user_id=999))
        text = client.send_message.call_args.args[1]
        self.assertIn("999", text)
        self.assertNotIn(self.user.email, text)

    def test_identification_mode_never_reveals_bookings_even_to_admin(self):
        self.make_hold()
        client = self.fake_client()
        handle_update(client, message_update("/today"), identify_only=True)
        client.send_message.assert_not_called()

    def test_allowed_admin_gets_card_and_menu(self):
        booking = self.make_hold()
        client = self.fake_client()
        handle_update(client, message_update(f"/booking {booking.pk}"))
        text = client.send_message.call_args.args[1]
        self.assertIn(self.user.email, text)
        self.assertIn("15:00–15:30 (Москва)", text)
        self.assertTrue(client.send_message.call_args.kwargs["menu"])

    def test_rate_limit_caps_admin_command_processing(self):
        client = self.fake_client()
        for _ in range(25):
            handle_update(client, message_update("/stats"))
        self.assertEqual(client.send_message.call_count, 20)

    def test_schedule_pagination_excludes_cancelled_and_expired_holds(self):
        start = datetime.combine(next_day(), time(9), tzinfo=salon_zone())
        for number in range(7):
            self.pay(self.make_hold(starts_at=start + timedelta(minutes=number * 30)))
        cancelled = self.make_hold(starts_at=start + timedelta(hours=4))
        cancel(booking_id=cancelled.pk, customer=self.user)
        expired = self.make_hold(starts_at=start + timedelta(hours=5))
        Booking.objects.filter(pk=expired.pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        first = command_reply(f"/date {next_day()} 1", timezone.now())
        second = command_reply(f"/date {next_day()} 2", timezone.now())
        self.assertIn("Всего: 7", first)
        self.assertEqual(first.count("/booking"), 5)
        self.assertEqual(second.count("/booking"), 2)
        self.assertNotIn(str(cancelled.pk), first + second)
        self.assertNotIn(str(expired.pk), first + second)
        self.assertLess(len(first), 4096)

    def test_schedule_uses_moscow_day_at_utc_boundary(self):
        booking = self.pay(self.make_hold())
        midnight = datetime.combine(next_day(), time(0, 15), tzinfo=salon_zone())
        reply = command_reply("/today", midnight)
        self.assertIn(str(booking.pk), reply)

    def test_invalid_input_is_handled_without_query_errors(self):
        for command in ("/booking nope", "/date 2026-02-31", "/date 9999-12-31", "/today -1", "/today 1 extra"):
            with self.subTest(command=command):
                reply = command_reply(command, timezone.now())
                self.assertIn("Проверьте", reply)

    def test_card_retains_custom_names_and_neutralizes_control_characters(self):
        booking = self.make_hold()
        booking.employee_name = "Мастер\nПоддельный статус"
        booking.service_name = "Авторская стрижка"
        self.assertIn("Мастер Поддельный статус", booking_card(booking))
        self.assertIn("Авторская стрижка", booking_card(booking))

    def test_worker_reuses_persisted_offset_and_skips_duplicate_update(self):
        TelegramCursor.objects.create(bot_id=BOT_ID, next_update_id=8)
        client = self.fake_client()
        def api(method, **kwargs):
            if method == "getMe":
                return {"id": BOT_ID, "is_bot": True}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getUpdates":
                self.assertEqual(kwargs["offset"], 8)
                return [message_update("/whoami", update_id=7), message_update("/whoami", update_id=8)]
            return True
        client.call.side_effect = api
        with patch("booking.management.commands.run_telegram_bot.TelegramClient", return_value=client):
            call_command("run_telegram_bot", once=True, stdout=io.StringIO())
        self.assertEqual(client.send_message.call_count, 1)
        self.assertEqual(TelegramCursor.objects.get().next_update_id, 9)

    def test_existing_webhook_is_not_deleted(self):
        client = self.fake_client()
        client.call.side_effect = [{"id": BOT_ID, "is_bot": True}, {"url": "https://example.test/hook"}]
        with patch("booking.management.commands.run_telegram_bot.TelegramClient", return_value=client):
            with self.assertRaisesMessage(CommandError, "active webhook"):
                call_command("run_telegram_bot", once=True, stdout=io.StringIO())
        self.assertEqual(client.call.call_count, 2)

    def test_web_booking_payment_and_refund_reach_the_fake_bot(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("booking-create"), {
            "employee": self.employee.pk, "date": next_day().isoformat(),
            "time": "15:00", "request_key": uuid.uuid4(),
        })
        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get()
        url = reverse("booking-checkout", args=[booking.pk])
        checkout = self.client.get(url)
        self.assertEqual(self.client.post(url, {"action_token": checkout.context["success_token"]}).status_code, 302)
        self.assertEqual(self.client.post(reverse("booking-cancel", args=[booking.pk])).status_code, 302)
        transport = self.fake_client()
        self.assertEqual(deliver_pending(transport)["sent"], 3)
        texts = [call.args[1] for call in transport.send_message.call_args_list]
        self.assertIn("Новая запись", texts[0])
        self.assertIn("Запись подтверждена", texts[1])
        self.assertIn("Полный демо-возврат выполнен", texts[2])

    def test_admin_retry_only_requeues_failed_deliveries(self):
        booking = self.make_hold()
        self.pay(booking)
        TelegramDelivery.objects.filter(event="reserved").update(status="sent", attempts=1)
        TelegramDelivery.objects.filter(event="confirmed").update(status="failed", attempts=5)
        model_admin = admin.site._registry[TelegramDelivery]
        with patch.object(model_admin, "message_user"):
            model_admin.retry_failed(Mock(), TelegramDelivery.objects.all())
        self.assertEqual(TelegramDelivery.objects.get(event="reserved").status, "sent")
        retried = TelegramDelivery.objects.get(event="confirmed")
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.attempts, 0)


class TelegramTransportTests(SimpleTestCase):
    def test_invalid_token_errors_never_include_the_value(self):
        secret = "secret-invalid-value"
        with self.assertRaises(ImproperlyConfigured) as context:
            bot_id_from_token(secret)
        self.assertNotIn(secret, str(context.exception))

    def test_network_error_does_not_expose_url_or_token(self):
        client = TelegramClient(TOKEN)
        client._opener.open = Mock(side_effect=URLError("https://api.telegram.org/bot" + TOKEN))
        with self.assertRaises(TelegramError) as context:
            client.send_message(STAFF_ID, "private")
        self.assertNotIn(TOKEN, str(context.exception))

    def test_http_error_respects_retry_after_and_redacts_description(self):
        client = TelegramClient(TOKEN)
        body = io.BytesIO(json.dumps({"description": TOKEN, "parameters": {"retry_after": 90}}).encode())
        error = HTTPError("https://example.test/", 429, "echo " + TOKEN, {}, body)
        client._opener.open = Mock(side_effect=error)
        with self.assertRaises(TelegramError) as context:
            client.send_message(STAFF_ID, "private")
        self.assertEqual(context.exception.retry_after, 90)
        self.assertNotIn(TOKEN, str(context.exception))

    def test_send_uses_https_post_plain_text_and_protected_content(self):
        client = TelegramClient(TOKEN)
        response = io.BytesIO(b'{"ok": true, "result": {"message_id": 1}}')
        client._opener.open = Mock(return_value=response)
        client.send_message(STAFF_ID, "<b>custom name</b>", menu=True)
        request = client._opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://api.telegram.org/"))
        self.assertEqual(request.method, "POST")
        payload = json.loads(request.data)
        self.assertTrue(payload["protect_content"])
        self.assertNotIn("parse_mode", payload)
        self.assertIn("reply_markup", payload)

    @override_settings(TELEGRAM_ENABLED=False, TELEGRAM_ADMIN_IDS=())
    def test_unconfigured_normal_worker_fails_before_contacting_telegram(self):
        with patch("booking.management.commands.run_telegram_bot.TelegramClient") as client:
            with self.assertRaisesMessage(CommandError, "--identify"):
                call_command("run_telegram_bot", once=True)
        client.assert_not_called()
