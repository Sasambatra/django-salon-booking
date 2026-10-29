from datetime import date, datetime, timedelta, timezone

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone

from booking.models import Employee, Service
from config.forms import EmployeeChoiceForm
from exercises.booking_rules import can_cancel, fits_opening_hours


@override_settings(LANGUAGE_CODE="en")
class BookingPagesTests(TestCase):
    fixtures = ["services.json"]

    def setUp(self):
        self.employee = Employee.objects.create(name="Practice employee")
        self.day = django_timezone.localdate() + timedelta(days=1)
        while self.day.weekday() == 6:
            self.day += timedelta(days=1)

    def test_service_page_shows_price_and_duration(self):
        response = self.client.get(reverse("service-card"))
        self.assertContains(response, "Price: 300 RUB")
        self.assertContains(response, "Duration: 30 minutes")

    def test_service_page_links_to_opening_hours(self):
        response = self.client.get(reverse("service-card"))
        hours_url = reverse("opening-hours")
        self.assertContains(response, f'href="{hours_url}"')

    def test_opening_hours_and_return_link(self):
        response = self.client.get(reverse("opening-hours"))
        service_url = reverse("service-card")
        self.assertContains(response, "Monday–Saturday: 09:00–18:00")
        self.assertContains(response, "Sunday: closed")
        self.assertContains(response, f'href="{service_url}"')

    def test_service_list_links_to_haircut_details(self):
        response = self.client.get(reverse("service-list"))
        self.assertContains(response, "Haircut appointment")
        self.assertContains(response, "Price: 300 RUB")
        self.assertContains(response, "Duration: 30 minutes")
        self.assertNotContains(response, "No services are currently listed.")
        detail_url = reverse("service-card")
        self.assertContains(response, f'href="{detail_url}"')

    def test_empty_service_list_shows_message(self):
        html = render_to_string("booking/service_list.html", {"services": []})
        self.assertIn("No services are currently listed.", html)
        self.assertNotIn("View haircut details", html)

    def test_date_form_starts_without_errors(self):
        response = self.client.get(
            reverse("choose-date"), {"employee": self.employee.pk}
        )
        self.assertContains(response, 'type="date"')
        self.assertNotContains(response, "This field is required.")

    def test_date_form_accepts_a_valid_date(self):
        response = self.client.get(
            reverse("choose-date"),
            {"date": self.day.isoformat(), "employee": self.employee.pk},
        )
        self.assertContains(response, "Selected date:")
        self.assertEqual(response.context["selected_date"], self.day)
        self.assertEqual(response.context["employee"], self.employee)
        self.assertContains(response, "Selected employee: Practice employee")

    def test_date_form_rejects_an_invalid_date(self):
        response = self.client.get(
            reverse("choose-date"),
            {"date": "not-a-date", "employee": self.employee.pk},
        )
        self.assertContains(response, "Enter a valid date.")
        self.assertNotContains(response, "Selected date:")

    def test_pages_read_the_saved_service_name(self):
        Service.objects.filter(pk=1).update(name="Database haircut")
        for route in ("service-list", "service-card", "choose-date", "choose-employee"):
            with self.subTest(route=route):
                params = {"employee": self.employee.pk} if route == "choose-date" else {}
                response = self.client.get(reverse(route), params)
                self.assertContains(response, "Database haircut")

    def test_service_list_handles_an_empty_database(self):
        Service.objects.all().delete()
        response = self.client.get(reverse("service-list"))
        self.assertContains(response, "No services are currently listed.")
        self.assertNotContains(response, "View haircut details")

    def test_missing_haircut_returns_404(self):
        Service.objects.filter(pk=1).delete()
        for route in ("service-card", "choose-date", "choose-employee"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 404)


class CancellationRulesTests(SimpleTestCase):
    def test_three_hours_before_start_is_allowed(self):
        starts_at = datetime(2030, 1, 15, 15, 0, tzinfo=timezone.utc)
        now = starts_at - timedelta(hours=3)
        self.assertTrue(can_cancel(starts_at, now))

    def test_exactly_two_hours_before_start_is_allowed(self):
        starts_at = datetime(2030, 1, 15, 15, 0, tzinfo=timezone.utc)
        now = starts_at - timedelta(hours=2)
        self.assertTrue(can_cancel(starts_at, now))

    def test_less_than_two_hours_before_start_is_rejected(self):
        starts_at = datetime(2030, 1, 15, 15, 0, tzinfo=timezone.utc)
        now = starts_at - timedelta(hours=1, minutes=59)
        self.assertFalse(can_cancel(starts_at, now))


class OpeningHoursRulesTests(SimpleTestCase):
    def test_opening_time_is_allowed(self):
        starts_at = datetime(2030, 1, 7, 9, 0)
        self.assertTrue(fits_opening_hours(starts_at, 30))

    def test_before_opening_is_rejected(self):
        starts_at = datetime(2030, 1, 7, 8, 59)
        self.assertFalse(fits_opening_hours(starts_at, 30))

    def test_saturday_last_start_is_allowed(self):
        starts_at = datetime(2030, 1, 5, 17, 30)
        self.assertTrue(fits_opening_hours(starts_at, 30))

    def test_finishing_after_closing_is_rejected(self):
        starts_at = datetime(2030, 1, 5, 17, 31)
        self.assertFalse(fits_opening_hours(starts_at, 30))

    def test_sunday_is_rejected(self):
        starts_at = datetime(2030, 1, 6, 10, 0)
        self.assertFalse(fits_opening_hours(starts_at, 30))


@override_settings(LANGUAGE_CODE="en")
class EmployeeChoiceFormTests(TestCase):
    def setUp(self):
        self.active = Employee.objects.create(name="Active employee")
        self.inactive = Employee.objects.create(
            name="Inactive employee",
            is_active=False,
        )

    def test_active_employee_is_accepted(self):
        form = EmployeeChoiceForm({"employee": str(self.active.pk)})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["employee"], self.active)

    def test_only_active_employees_are_listed(self):
        html = str(EmployeeChoiceForm())
        self.assertIn(self.active.name, html)
        self.assertNotIn(self.inactive.name, html)

    def test_invalid_employee_choices_are_rejected(self):
        values = (
            "",
            str(self.inactive.pk),
            str(self.inactive.pk + 1),
            "not-an-id",
        )
        for value in values:
            with self.subTest(value=value):
                form = EmployeeChoiceForm({"employee": value})
                self.assertFalse(form.is_valid())
                self.assertIn("employee", form.errors)


@override_settings(LANGUAGE_CODE="en")
class EmployeePageTests(TestCase):
    fixtures = ["services.json"]

    def setUp(self):
        self.active = Employee.objects.create(name="Active employee")
        self.inactive = Employee.objects.create(
            name="Inactive employee", is_active=False
        )

    def test_page_only_lists_active_employees(self):
        response = self.client.get(reverse("choose-employee"))
        self.assertContains(response, self.active.name)
        self.assertNotContains(response, self.inactive.name)
        self.assertNotContains(response, "This field is required.")

    def test_valid_employee_choice_is_shown(self):
        response = self.client.get(
            reverse("choose-employee"), {"employee": self.active.pk}
        )
        self.assertContains(response, "Selected employee: Active employee")
        self.assertEqual(response.context["selected_employee"], self.active)
        next_url = f'{reverse("choose-date")}?employee={self.active.pk}'
        self.assertContains(response, f'href="{next_url}"')

    def test_invalid_employee_choice_is_rejected(self):
        for value in ("", str(self.inactive.pk), str(self.inactive.pk + 1), "not-an-id"):
            with self.subTest(value=value):
                response = self.client.get(
                    reverse("choose-employee"), {"employee": value}
                )
                self.assertIn("employee", response.context["form"].errors)
                self.assertIsNone(response.context["selected_employee"])
                self.assertNotContains(response, "Selected employee:")

    def test_no_active_employees_shows_an_empty_message(self):
        Employee.objects.update(is_active=False)
        response = self.client.get(reverse("choose-employee"))
        self.assertContains(response, "No employees are currently listed for booking.")
        self.assertNotContains(response, '<select')
        self.assertIsNone(response.context["selected_employee"])

    def test_date_page_keeps_the_employee_without_initial_date_errors(self):
        response = self.client.get(
            reverse("choose-date"), {"employee": self.active.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["employee"], self.active)
        self.assertIsNone(response.context["selected_date"])
        self.assertContains(response, f'name="employee" value="{self.active.pk}"')
        self.assertNotContains(response, "This field is required.")

    def test_date_errors_keep_the_selected_employee(self):
        response = self.client.get(
            reverse("choose-date"),
            {"employee": self.active.pk, "date": "not-a-date"},
        )
        self.assertContains(response, "Enter a valid date.")
        self.assertContains(response, self.active.name)
        self.assertEqual(response.context["employee"], self.active)
        self.assertIsNone(response.context["selected_date"])

    def test_date_page_requires_a_current_active_employee(self):
        for value in (None, "", str(self.inactive.pk), str(self.inactive.pk + 1), "not-an-id"):
            with self.subTest(value=value):
                params = {"date": "2030-01-15"}
                if value is not None:
                    params["employee"] = value
                response = self.client.get(reverse("choose-date"), params)
                self.assertRedirects(response, reverse("choose-employee"))

    def test_employee_deactivated_between_steps_must_be_reselected(self):
        response = self.client.get(
            reverse("choose-employee"), {"employee": self.active.pk}
        )
        self.assertEqual(response.context["selected_employee"], self.active)
        Employee.objects.filter(pk=self.active.pk).update(is_active=False)
        response = self.client.get(
            reverse("choose-date"),
            {"employee": self.active.pk, "date": "2030-01-15"},
        )
        self.assertRedirects(response, reverse("choose-employee"))
