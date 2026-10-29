"""Language selection must preserve bookings, safe redirects and translated flows."""
from datetime import datetime, time, timedelta
import gettext
from pathlib import Path
import uuid

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from booking.models import Booking, Employee, Payment, Service
from booking.presentation import service_title
from booking.services import reserve, salon_zone, validate_day
from scripts.compile_translations import compile_catalog, read_catalog


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LanguageTests(TestCase):
    fixtures = ["services.json"]

    @classmethod
    def setUpTestData(cls):
        cls.employee = Employee.objects.create(name="Анна Example")
        cls.customer = get_user_model().objects.create_user(
            "language_customer", "language@example.test", "OnlyForTests!83-words"
        )
        EmailAddress.objects.create(
            user=cls.customer, email=cls.customer.email, verified=True, primary=True
        )
        cls.day = timezone.localdate() + timedelta(days=1)
        while cls.day.weekday() == 6:
            cls.day += timedelta(days=1)

    def make_hold(self):
        return reserve(
            customer=self.customer, service_id=1, employee_id=self.employee.pk,
            starts_at=datetime.combine(self.day, time(15), tzinfo=salon_zone()),
            request_key=uuid.uuid4(),
        )

    def test_browser_language_and_regional_variants(self):
        cases = (
            ("ru-RU,ru;q=0.9,en;q=0.8", "ru", "Услуги"),
            ("en-US,en;q=0.9,ru;q=0.5", "en", "Services"),
            ("de,fr;q=0.9,en;q=0.8", "en", "Services"),
            ("en;q=0.2,ru;q=0.9", "ru", "Услуги"),
        )
        for header, code, label in cases:
            with self.subTest(header=header):
                response = self.client.get(reverse("service-list"), HTTP_ACCEPT_LANGUAGE=header)
                self.assertContains(response, label)
                self.assertContains(response, f'<html lang="{code}">')
                self.assertEqual(response["Content-Language"], code)
                self.assertIn("Accept-Language", response["Vary"])

    def test_missing_and_unsupported_preferences_fall_back_to_russian(self):
        for header in ("", "fr-FR,fr;q=0.9", "invalid-language"):
            with self.subTest(header=header):
                response = self.client.get(reverse("service-list"), HTTP_ACCEPT_LANGUAGE=header)
                self.assertEqual(response["Content-Language"], "ru")
                self.assertContains(response, "Цена: 300 RUB")

    def test_explicit_choice_overrides_browser_language_on_later_pages(self):
        self.client.post(reverse("set_language"), {"language": "en", "next": reverse("service-list")})
        for route in ("service-list", "opening-hours", "account_login"):
            response = self.client.get(reverse(route), HTTP_ACCEPT_LANGUAGE="ru-RU")
            self.assertEqual(response["Content-Language"], "en")
            self.assertContains(response, '<html lang="en">')

    def test_invalid_cookie_falls_back_to_browser_preference(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "unsupported"
        response = self.client.get(reverse("service-list"), HTTP_ACCEPT_LANGUAGE="en-GB")
        self.assertEqual(response["Content-Language"], "en")

    def test_switch_keeps_employee_date_and_encoded_return_link(self):
        next_url = f'{reverse("choose-date")}?employee={self.employee.pk}&date={self.day.isoformat()}'
        response = self.client.post(reverse("set_language"), {"language": "en", "next": next_url})
        self.assertEqual(response["Location"], next_url)
        response = self.client.get(next_url, HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, self.employee.name)
        self.assertContains(response, f'name="date" value="{self.day.isoformat()}"')
        self.assertContains(response, f'name="employee" value="{self.employee.pk}"')
        self.assertEqual(response["Content-Language"], "en")
        self.assertEqual(Booking.objects.count(), 0)

    def test_switch_never_redirects_to_another_site(self):
        for target in ("https://other.example/", "//other.example/", r"/\other.example/"):
            with self.subTest(target=target):
                response = self.client.post(reverse("set_language"), {"language": "en", "next": target})
                self.assertEqual(response["Location"], "/")

    def test_switch_rejects_unsupported_languages(self):
        for code in ("fr", "ru-RU", "", "../en"):
            response = self.client.post(reverse("set_language"), {"language": code, "next": "/"})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)

    def test_switch_requires_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse("set_language"), {"language": "en"}).status_code, 405)
        self.assertEqual(client.post(reverse("set_language"), {"language": "en"}).status_code, 403)
        client.get(reverse("service-list"))
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(
            reverse("set_language"),
            {"language": "en", "next": "/", "csrfmiddlewaretoken": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

    @override_settings(LANGUAGE_COOKIE_SECURE=True)
    def test_remembered_choice_has_expected_cookie_protections(self):
        response = self.client.post(reverse("set_language"), {"language": "ru", "next": "/"})
        cookie = response.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["max-age"], 365 * 24 * 60 * 60)

    def test_public_and_account_pages_translate_in_both_languages(self):
        pages = (
            ("about-studio", "О студии", "About the studio"),
            ("service-list", "Услуги", "Services"),
            ("service-card", "Цена: 300 RUB", "Price: 300 RUB"),
            ("choose-employee", "Выбрать мастера", "Choose an employee"),
            ("opening-hours", "Воскресенье: выходной", "Sunday: closed"),
            ("account_login", "Войти", "Sign In"),
            ("account_signup", "Зарегистрируйтесь", "Sign Up"),
            ("account_reset_password", "Сброс пароля", "Password Reset"),
        )
        for route, russian, english in pages:
            for code, expected in (("ru", russian), ("en", english)):
                with self.subTest(route=route, code=code):
                    response = self.client.get(reverse(route), HTTP_ACCEPT_LANGUAGE=code)
                    self.assertContains(response, expected)
                    self.assertEqual(response["Content-Language"], code)

    def test_date_labels_errors_and_calendar_text_are_translated(self):
        url = reverse("choose-date")
        response = self.client.get(url, {"employee": self.employee.pk, "date": "bad"}, HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, "Удобная дата")
        self.assertContains(response, "Введите правильную дату.")
        response = self.client.get(url, {"employee": self.employee.pk, "date": self.day.isoformat()}, HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, "Свободное время")
        self.assertContains(response, "Выбранный мастер: Анна Example")
        self.assertContains(response, "09:00")
        self.assertContains(response, "17:30")
        self.assertContains(response, "Цена: 300 RUB")
        self.assertContains(response, "Длительность: 30 мин.")
        self.assertContains(response, f'value="{self.day.isoformat()}"')

    def test_business_validation_translates_with_dynamic_values(self):
        with translation.override("ru"):
            with self.assertRaisesMessage(ValidationError, "60"):
                validate_day(timezone.localdate() + timedelta(days=61))
            with self.assertRaisesMessage(ValidationError, "Выберите сегодняшнюю или будущую дату."):
                validate_day(timezone.localdate() - timedelta(days=1))

    def test_booking_checkout_switch_and_refund_are_translated_without_data_changes(self):
        self.client.force_login(self.customer)
        booking = self.make_hold()
        url = reverse("booking-checkout", args=[booking.pk])
        response = self.client.get(url, HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, "Имитировать успешную оплату")
        self.assertContains(response, 'data-remaining="осталось."')
        token = response.context["success_token"]
        self.client.post(reverse("set_language"), {"language": "en", "next": url})
        self.assertContains(self.client.get(url), "Simulate successful payment")
        booking.refresh_from_db()
        self.assertEqual(booking.service_name, "Haircut appointment")
        self.assertEqual(booking.price_rub, 300)
        self.assertEqual(booking.starts_at.hour, 12)  # 15:00 Moscow, stored as UTC.
        self.client.post(reverse("set_language"), {"language": "ru", "next": url})
        self.client.post(url, {"action_token": token})
        detail_url = reverse("booking-detail", args=[booking.pk])
        self.assertContains(self.client.get(detail_url), "Подтверждена")
        self.client.post(reverse("booking-cancel", args=[booking.pk]))
        response = self.client.get(detail_url)
        self.assertContains(response, "Демооплата в размере 300 RUB полностью возвращена.")
        self.assertContains(response, "Отменена")
        booking.refresh_from_db()
        self.assertEqual(booking.payment.status, Payment.Status.REFUNDED)
        self.assertEqual(booking.service_name, "Haircut appointment")

    def test_expiry_notice_uses_state_instead_of_english_label(self):
        self.client.force_login(self.customer)
        booking = self.make_hold()
        Booking.objects.filter(pk=booking.pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(reverse("booking-detail", args=[booking.pk]), HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, "Время ожидания оплаты истекло.")
        self.assertContains(response, "Время оплаты истекло")
        self.assertNotContains(response, "Ожидает демооплаты")

    def test_signup_email_uses_selected_language(self):
        for code in ("ru", "en"):
            with self.subTest(code=code):
                response = Client().post(
                    reverse("account_signup"),
                    {"email": f"signup-{code}@example.test",
                     "password1": "OnlyForTests!83-words", "password2": "OnlyForTests!83-words"},
                    HTTP_ACCEPT_LANGUAGE=code,
                )
                self.assertEqual(response.status_code, 302)
                body = mail.outbox[-1].body
                self.assertIn("Для подтверждения" if code == "ru" else "To confirm this is correct", body)
                self.assertIn("/accounts/confirm-email/", body)

    def test_password_recovery_email_uses_selected_language(self):
        for code in ("ru", "en"):
            response = Client().post(
                reverse("account_reset_password"), {"email": self.customer.email}, HTTP_ACCEPT_LANGUAGE=code
            )
            self.assertEqual(response.status_code, 302)
            body = mail.outbox[-1].body
            self.assertIn("сброс пароля" if code == "ru" else "password reset", body)
            self.assertIn("/accounts/password/reset/key/", body)

    def test_custom_service_and_employee_names_remain_entered_values_and_are_escaped(self):
        with translation.override("ru"):
            self.assertEqual(service_title("Haircut appointment"), "Стрижка")
            self.assertEqual(service_title("Custom studio service"), "Custom studio service")
        Service.objects.filter(pk=1).update(name="<script>Example</script>")
        response = self.client.get(reverse("service-list"), HTTP_ACCEPT_LANGUAGE="ru")
        self.assertContains(response, "&lt;script&gt;Example&lt;/script&gt;")
        self.assertNotContains(response, "<script>Example</script>")
        self.assertEqual(Employee.objects.get(pk=self.employee.pk).name, "Анна Example")

    def test_compiled_catalog_matches_editable_source_and_runtime(self):
        source = Path(settings.BASE_DIR) / "locale/ru/LC_MESSAGES/django.po"
        messages = read_catalog(source)
        self.assertEqual(source.with_suffix(".mo").read_bytes(), compile_catalog(messages))
        with source.with_suffix(".mo").open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        for original, expected in messages.items():
            if original:
                self.assertEqual(catalog.gettext(original), expected)
