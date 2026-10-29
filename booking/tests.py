from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from booking.models import Service


class ServiceValidationTests(SimpleTestCase):
    def test_positive_duration_passes_field_validation(self):
        for duration in (1, 30):
            with self.subTest(duration=duration):
                service = Service(
                    name="Haircut appointment",
                    price_rub=300,
                    duration_minutes=duration,
                )
                service.clean_fields()

    def test_zero_duration_is_rejected_by_field_validation(self):
        service = Service(
            name="Haircut appointment", price_rub=300, duration_minutes=0
        )
        with self.assertRaises(ValidationError) as caught:
            service.clean_fields()
        self.assertIn("duration_minutes", caught.exception.message_dict)

    def test_negative_duration_is_rejected_by_field_validation(self):
        service = Service(
            name="Haircut appointment", price_rub=300, duration_minutes=-1
        )
        with self.assertRaises(ValidationError) as caught:
            service.clean_fields()
        self.assertIn("duration_minutes", caught.exception.message_dict)


class ServiceDatabaseTests(TestCase):
    def test_service_can_be_saved_and_loaded(self):
        service = Service.objects.create(
            name="Haircut appointment", price_rub=300, duration_minutes=30
        )
        saved_service = Service.objects.get(pk=service.pk)
        self.assertEqual(saved_service.name, "Haircut appointment")
        self.assertEqual(saved_service.price_rub, 300)
        self.assertEqual(saved_service.duration_minutes, 30)

    def test_database_rejects_zero_duration_without_field_validation(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Service.objects.create(
                    name="Haircut appointment", price_rub=300, duration_minutes=0
                )
