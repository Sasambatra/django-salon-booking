import csv
import io
import stat
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.mfa.models import Authenticator
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone

from booking.adapters import EncryptedMFAAdapter
from booking.admin import BookingAdmin
from booking.models import Booking, Employee, Payment, PaymentEvent, Service, TimeOff
from booking.services import available_starts, cancel, reserve, salon_zone, settle_demo_payment, validate_day
from booking.security import TrustedProxyMiddleware

PASSWORD = "StudioTest-8h!clear"


def next_day():
    day = timezone.localdate() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


class BookingData:
    fixtures = ["services.json"]

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("customer", "customer@example.test", PASSWORD)
        cls.other = get_user_model().objects.create_user("other", "other@example.test", PASSWORD)
        for user in (cls.user, cls.other):
            EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        cls.service = Service.objects.get(pk=1)
        cls.employee = Employee.objects.create(name="First stylist")
        cls.second = Employee.objects.create(name="Second stylist")

    def make_hold(self, **changes):
        values = dict(customer=self.user, service_id=self.service.pk, employee_id=self.employee.pk,
                      starts_at=datetime.combine(next_day(), time(15), tzinfo=salon_zone()), request_key=uuid.uuid4())
        values.update(changes)
        return reserve(**values)

    def pay(self, booking, **changes):
        values = dict(booking_id=booking.pk, customer=self.user, outcome="success", event_key=str(uuid.uuid4()))
        values.update(changes)
        return settle_demo_payment(**values)


class BookingRulesTests(BookingData, TestCase):
    def test_service_price_must_be_positive(self):
        self.service.price_rub = 0
        with self.assertRaises(ValidationError):
            self.service.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Service.objects.filter(pk=self.service.pk).update(price_rub=0)

    def test_day_validation_rejects_past_sundays_and_far_future(self):
        today = timezone.localdate()
        sunday = today + timedelta(days=(6 - today.weekday()) % 7)
        for day in (today - timedelta(days=1), sunday, today + timedelta(days=61)):
            with self.subTest(day=day), self.assertRaises(ValidationError):
                validate_day(day)

    def test_opening_and_last_start_are_correct(self):
        starts = available_starts(self.service, self.employee, next_day())
        self.assertEqual(len(starts), 18)
        self.assertEqual(starts[0].strftime("%H:%M"), "09:00")
        self.assertEqual(starts[-1].strftime("%H:%M"), "17:30")

    def test_time_off_removes_overlapping_times(self):
        start = datetime.combine(next_day(), time(15), tzinfo=salon_zone())
        TimeOff.objects.create(employee=self.employee, starts_at=start, ends_at=start + timedelta(hours=1))
        times = [value.strftime("%H:%M") for value in available_starts(self.service, self.employee, next_day())]
        self.assertNotIn("15:00", times)
        self.assertNotIn("15:30", times)
        self.assertIn("14:30", times)

    def test_hold_creates_payment_and_reserves_only_its_employee(self):
        booking = self.make_hold()
        self.assertEqual(booking.status, Booking.Status.HELD)
        self.assertEqual(booking.payment.status, Payment.Status.PENDING)
        self.assertNotIn(booking.starts_at, available_starts(self.service, self.employee, next_day()))
        self.assertIn(booking.starts_at, available_starts(self.service, self.second, next_day()))

    def test_adjacent_appointment_is_allowed(self):
        first = self.make_hold()
        second = self.make_hold(starts_at=first.ends_at)
        self.assertEqual(second.starts_at, first.ends_at)

    def test_different_start_overlapping_a_longer_service_is_rejected(self):
        self.service.duration_minutes = 45
        self.service.save()
        first = self.make_hold()
        with self.assertRaises(ValidationError):
            self.make_hold(starts_at=first.starts_at + timedelta(minutes=30))

    def test_repeated_request_is_idempotent(self):
        first = self.make_hold()
        second = self.make_hold(request_key=first.request_key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_request_key_cannot_be_reused_for_another_customer(self):
        first = self.make_hold()
        with self.assertRaises(ValidationError):
            self.make_hold(customer=self.other, request_key=first.request_key)

    def test_inactive_employee_and_off_grid_time_are_rejected(self):
        self.employee.is_active = False
        self.employee.save()
        with self.assertRaises(ValidationError):
            self.make_hold()
        self.employee.is_active = True
        self.employee.save()
        with self.assertRaises(ValidationError):
            self.make_hold(starts_at=datetime.combine(next_day(), time(15, 5), tzinfo=salon_zone()))

    def test_payment_success_and_repeated_results_charge_once(self):
        booking = self.pay(self.make_hold())
        booking = self.pay(booking)
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_payment_failure_releases_time(self):
        booking = self.pay(self.make_hold(), outcome="failure")
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment.status, Payment.Status.FAILED)
        self.assertIn(booking.starts_at, available_starts(self.service, self.employee, next_day()))

    def test_expired_hold_cannot_be_paid_and_can_be_replaced(self):
        first = self.make_hold()
        Booking.objects.filter(pk=first.pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        first = self.pay(first)
        self.assertEqual(first.status, Booking.Status.EXPIRED)
        self.assertEqual(first.payment.status, Payment.Status.EXPIRED)
        second = self.make_hold()
        self.assertNotEqual(first.pk, second.pk)

    def test_payment_amount_mismatch_is_rejected(self):
        booking = self.make_hold()
        Payment.objects.filter(booking=booking).update(amount_rub=1)
        with self.assertRaises(ValidationError):
            self.pay(booking)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HELD)

    def test_payment_event_cannot_settle_another_booking(self):
        key = str(uuid.uuid4())
        self.pay(self.make_hold(), event_key=key)
        second = self.make_hold(employee_id=self.second.pk)
        with self.assertRaises(ValidationError):
            self.pay(second, event_key=key)

    def test_full_refund_at_exact_cancellation_boundary_and_retry(self):
        booking = self.pay(self.make_hold())
        cutoff = booking.starts_at - timedelta(hours=2)
        cancel(booking_id=booking.pk, customer=self.user, now=cutoff)
        cancel(booking_id=booking.pk, customer=self.user, now=cutoff)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment.status, Payment.Status.REFUNDED)
        self.assertEqual(PaymentEvent.objects.filter(outcome="full_refund").count(), 1)

    def test_late_cancellation_is_rejected(self):
        booking = self.pay(self.make_hold())
        with self.assertRaises(ValidationError):
            cancel(booking_id=booking.pk, customer=self.user, now=booking.starts_at - timedelta(hours=2) + timedelta(seconds=1))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_cancelling_an_unpaid_hold_does_not_create_a_refund(self):
        booking = cancel(booking_id=self.make_hold().pk, customer=self.user)
        self.assertEqual(booking.payment.status, Payment.Status.FAILED)
        self.assertEqual(PaymentEvent.objects.count(), 0)

    def test_agreed_price_names_and_duration_survive_catalog_changes(self):
        booking = self.make_hold()
        Service.objects.filter(pk=self.service.pk).update(name="New offer", price_rub=400, duration_minutes=45)
        Employee.objects.filter(pk=self.employee.pk).update(name="New name", is_active=False)
        booking.refresh_from_db()
        self.assertEqual(booking.price_rub, 300)
        self.assertEqual(booking.service_name, "Haircut appointment")
        self.assertEqual(booking.employee_name, "First stylist")
        self.assertEqual(booking.ends_at - booking.starts_at, timedelta(minutes=30))
        with self.assertRaises(ProtectedError):
            self.employee.delete()

    def test_database_rejects_invalid_interval(self):
        booking = self.make_hold()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=booking.pk).update(ends_at=booking.starts_at)

    def test_time_off_validation_protects_existing_booking(self):
        booking = self.make_hold()
        block = TimeOff(employee=self.employee, starts_at=booking.starts_at, ends_at=booking.ends_at)
        with self.assertRaises(ValidationError):
            block.full_clean()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BookingWebTests(BookingData, TestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def post_hold(self, client=None, key=None):
        return (client or self.client).post(reverse("booking-create"), {"employee": self.employee.pk, "date": next_day().isoformat(), "time": "15:00", "request_key": key or uuid.uuid4()})

    def test_authenticated_customer_can_book_pay_and_refund(self):
        self.post_hold()
        booking = Booking.objects.get()
        checkout = self.client.get(reverse("booking-checkout", args=[booking.pk]))
        self.client.post(reverse("booking-checkout", args=[booking.pk]), {"action_token": checkout.context["success_token"]})
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.client.post(reverse("booking-cancel", args=[booking.pk]))
        booking.refresh_from_db()
        self.assertEqual(booking.payment.status, Payment.Status.REFUNDED)

    def test_duplicate_form_posts_create_one_hold(self):
        key = uuid.uuid4()
        self.post_hold(key=key)
        self.post_hold(key=key)
        self.assertEqual(Booking.objects.count(), 1)

    def test_anonymous_and_unverified_customers_cannot_book(self):
        anonymous = Client()
        self.assertEqual(self.post_hold(client=anonymous).status_code, 302)
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        self.post_hold()
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_creation_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse("booking-create")).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(self.post_hold(client=client).status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

    def test_other_customer_cannot_read_pay_or_cancel_booking(self):
        booking = self.make_hold()
        self.client.force_login(self.other)
        for route in ("booking-detail", "booking-checkout", "booking-cancel"):
            with self.subTest(route=route):
                url = reverse(route, args=[booking.pk])
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_unsigned_payment_result_cannot_confirm(self):
        booking = self.make_hold()
        self.client.post(reverse("booking-checkout", args=[booking.pk]), {"outcome": "success"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HELD)

    def test_payment_token_cannot_be_used_for_another_booking(self):
        first = self.make_hold()
        token = self.client.get(reverse("booking-checkout", args=[first.pk])).context["success_token"]
        second = self.make_hold(employee_id=self.second.pk)
        self.client.post(reverse("booking-checkout", args=[second.pk]), {"action_token": token})
        second.refresh_from_db()
        self.assertEqual(second.status, Booking.Status.HELD)

    def test_checkout_post_replay_does_not_double_pay(self):
        booking = self.make_hold()
        url = reverse("booking-checkout", args=[booking.pk])
        token = self.client.get(url).context["success_token"]
        self.client.post(url, {"action_token": token})
        self.client.post(url, {"action_token": token})
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_private_booking_pages_are_not_cached(self):
        response = self.client.get(reverse("booking-detail", args=[self.make_hold().pk]))
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_rate_limit_returns_retry_after(self):
        for _ in range(30):
            self.client.post(reverse("booking-create"), {})
        response = self.client.post(reverse("booking-create"), {})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")

    def test_staff_admin_requires_verified_email_and_mfa(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(response, reverse("mfa_activate_totp"), fetch_redirect_response=False)
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        self.assertRedirects(self.client.get(reverse("admin:index")), reverse("account_email"), fetch_redirect_response=False)

    def test_account_verification_and_password_recovery_pages_work(self):
        self.client.logout()
        response = self.client.post(reverse("account_signup"), {"email": "new@example.test", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(response.status_code, 302)
        address = EmailAddress.objects.get(email="new@example.test")
        self.assertFalse(address.verified)
        self.assertGreater(len(mail.outbox), 0)
        key = EmailConfirmationHMAC(address).key
        self.client.post(reverse("account_confirm_email", args=[key]))
        address.refresh_from_db()
        self.assertTrue(address.verified)
        response = self.client.post(reverse("account_reset_password"), {"email": "new@example.test"})
        self.assertEqual(response.status_code, 302)
        self.assertGreaterEqual(len(mail.outbox), 2)

    def test_mfa_secrets_are_encrypted(self):
        adapter = EncryptedMFAAdapter()
        encrypted = adapter.encrypt("test-authenticator-secret")
        self.assertNotIn("test-authenticator-secret", encrypted)
        self.assertEqual(adapter.decrypt(encrypted), "test-authenticator-secret")

    def test_csv_export_quotes_formula_cells(self):
        booking = self.make_hold()
        Booking.objects.filter(pk=booking.pk).update(service_name="=SUM(A1:A2)")
        request = RequestFactory().get("/admin/")
        request.user = self.user
        response = BookingAdmin(Booking, admin.site).export_csv(request, Booking.objects.all())
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[1][2], "'=SUM(A1:A2)")


@skipUnless(connection.vendor == "postgresql", "Concurrency requires PostgreSQL row locks")
class BookingConcurrencyTests(TransactionTestCase):
    fixtures = ["services.json"]

    def setUp(self):
        self.user = get_user_model().objects.create_user("racing", "racing@example.test", PASSWORD)
        self.employee = Employee.objects.create(name="One stylist")
        self.start = datetime.combine(next_day(), time(15), tzinfo=salon_zone())

    def race(self, starts):
        barrier = Barrier(2)
        def worker(start):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                reserve(customer=self.user, service_id=1, employee_id=self.employee.pk, starts_at=start, request_key=uuid.uuid4())
                return "reserved"
            except ValidationError:
                return "unavailable"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, starts))
        self.assertCountEqual(results, ["reserved", "unavailable"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_simultaneous_requests_cannot_book_the_same_time(self):
        self.race([self.start, self.start])

    def test_simultaneous_different_starts_cannot_overlap(self):
        Service.objects.filter(pk=1).update(duration_minutes=45)
        self.race([self.start, self.start + timedelta(minutes=30)])


@override_settings(LANGUAGE_CODE="en")
class OperationalTests(BookingData, TestCase):
    def test_fresh_demo_setup_seeds_data_without_duplicate_or_id_collisions(self):
        Employee.objects.all().delete()
        Service.objects.all().delete()
        with tempfile.TemporaryDirectory() as directory, override_settings(DEBUG=True, BASE_DIR=Path(directory)):
            call_command("setup_demo", stdout=io.StringIO())
            call_command("setup_demo", stdout=io.StringIO())
        self.assertEqual(Service.objects.get(pk=1).price_rub, 300)
        self.assertEqual(Employee.objects.count(), 2)
        Employee.objects.create(name="Another stylist")
        self.assertEqual(Employee.objects.count(), 3)

    def test_demo_owner_credentials_are_private_and_never_reset(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DEBUG=True, BASE_DIR=Path(directory)):
            output = io.StringIO()
            call_command("setup_demo", stdout=output)
            path = Path(directory) / ".local" / "owner-access.txt"
            content = path.read_text()
            password = next(line.split(": ", 1)[1] for line in content.splitlines() if line.startswith("Password:"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(password, output.getvalue())
            owner = get_user_model().objects.get(username="studio_owner")
            self.assertTrue(owner.check_password(password))
            call_command("setup_demo", stdout=output)
            self.assertEqual(path.read_text(), content)
            self.assertEqual(get_user_model().objects.filter(is_superuser=True).count(), 1)

    def test_security_headers_are_present(self):
        response = self.client.get(reverse("service-list"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("form-action 'self'", response["Content-Security-Policy"])

    @override_settings(TRUSTED_PROXY_CIDRS=[], SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_untrusted_proxy_cannot_spoof_https(self):
        request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.9", HTTP_X_FORWARDED_PROTO="https")
        response = TrustedProxyMiddleware(lambda req: HttpResponse(str(req.is_secure())))(request)
        self.assertEqual(response.content, b"False")

    @override_settings(TRUSTED_PROXY_CIDRS=["127.0.0.1/32"], SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_configured_proxy_passes_validated_client_identity(self):
        request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="203.0.113.8", HTTP_X_FORWARDED_PROTO="https")
        response = TrustedProxyMiddleware(lambda req: JsonResponse({"ip": req.META["REMOTE_ADDR"], "secure": req.is_secure()}))(request)
        self.assertEqual(response.content, b'{"ip": "203.0.113.8", "secure": true}')

    def test_health_endpoint_reports_database_failure(self):
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})
        with patch("booking.views.connection.cursor", side_effect=DatabaseError):
            response = Client().get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    def test_expired_hold_receipt_does_not_claim_payment_is_pending(self):
        booking = self.make_hold()
        Booking.objects.filter(pk=booking.pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.user)
        response = self.client.get(reverse("booking-detail", args=[booking.pk]))
        self.assertContains(response, "Payment time expired")
        self.assertNotContains(response, "Awaiting demo payment")
