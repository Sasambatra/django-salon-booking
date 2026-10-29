"""Booking rules shared by the site and administrator actions."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from booking.models import Booking, Employee, Payment, PaymentEvent, Service, TimeOff
from booking.telegram import queue_booking_event


def salon_zone():
    return ZoneInfo(settings.TIME_ZONE)


def validate_day(day, now=None):
    today = timezone.localtime(now or timezone.now(), salon_zone()).date()
    if day < today:
        raise ValidationError(_("Choose today or a future date."))
    if day > today + timedelta(days=settings.BOOKING_ADVANCE_DAYS):
        raise ValidationError(_("Choose a date within the next %(days)s days.") % {"days": settings.BOOKING_ADVANCE_DAYS})
    if day.weekday() == 6:
        raise ValidationError(_("The studio is closed on Sundays. Choose Monday–Saturday."))


def active_bookings(employee, now):
    return Booking.objects.filter(employee=employee).filter(
        Q(status=Booking.Status.CONFIRMED) | Q(status=Booking.Status.HELD, hold_expires_at__gt=now)
    )


def available_starts(service, employee, day, now=None):
    now = now or timezone.now()
    validate_day(day, now)
    if not employee.is_active or service.duration_minutes <= 0 or service.price_rub <= 0:
        return []
    opening = datetime.combine(day, time(9), tzinfo=salon_zone())
    closing = datetime.combine(day, time(18), tzinfo=salon_zone())
    occupied = list(active_bookings(employee, now).filter(starts_at__lt=closing, ends_at__gt=opening).values_list("starts_at", "ends_at"))
    occupied += list(TimeOff.objects.filter(employee=employee, starts_at__lt=closing, ends_at__gt=opening).values_list("starts_at", "ends_at"))
    duration = timedelta(minutes=service.duration_minutes)
    starts, candidate = [], opening
    while candidate + duration <= closing:
        end = candidate + duration
        if candidate > now and not any(candidate < taken_end and end > taken_start for taken_start, taken_end in occupied):
            starts.append(candidate)
        candidate += timedelta(minutes=30)
    return starts


def expire_employee_holds(employee, now):
    expired = Booking.objects.filter(employee=employee, status=Booking.Status.HELD, hold_expires_at__lte=now)
    expired_ids = list(expired.values_list("pk", flat=True))
    Payment.objects.filter(booking__in=expired, status=Payment.Status.PENDING).update(status=Payment.Status.EXPIRED, updated_at=now)
    count = expired.update(status=Booking.Status.EXPIRED)
    for booking in Booking.objects.filter(pk__in=expired_ids).select_related("customer", "payment"):
        queue_booking_event(booking, "expired")
    return count


def expire_due_holds(now=None):
    """Reconcile expiry through the same employee locks as booking writes."""
    now = now or timezone.now()
    employee_ids = (Booking.objects.filter(status=Booking.Status.HELD, hold_expires_at__lte=now)
                    .order_by().values_list("employee_id", flat=True).distinct())
    count = 0
    for employee_id in employee_ids:
        with transaction.atomic():
            employee = Employee.objects.select_for_update().get(pk=employee_id)
            count += expire_employee_holds(employee, now)
    return count


def effective_status(booking):
    if booking.status == Booking.Status.HELD and booking.hold_expires_at <= timezone.now():
        return Booking.Status.EXPIRED
    return booking.status


def reserve(*, customer, service_id, employee_id, starts_at, request_key, now=None):
    now = now or timezone.now()
    if timezone.is_naive(starts_at):
        raise ValidationError(_("The appointment time must include a time zone."))
    try:
        with transaction.atomic():
            employee = Employee.objects.select_for_update().get(pk=employee_id)
            service = Service.objects.get(pk=service_id)
            existing = Booking.objects.filter(request_key=request_key).first()
            if existing:
                if existing.customer_id != customer.pk or existing.employee_id != employee_id or existing.service_id != service_id or existing.starts_at != starts_at:
                    raise ValidationError(_("This request has already been used. Choose the appointment again."))
                return existing
            expire_employee_holds(employee, now)
            day = timezone.localtime(starts_at, salon_zone()).date()
            if starts_at not in available_starts(service, employee, day, now):
                raise ValidationError(_("That time is no longer available. Choose another time."))
            booking = Booking.objects.create(
                request_key=request_key, customer=customer, service=service,
                employee=employee, service_name=service.name, employee_name=employee.name,
                starts_at=starts_at, ends_at=starts_at + timedelta(minutes=service.duration_minutes),
                price_rub=service.price_rub, hold_expires_at=now + timedelta(minutes=settings.BOOKING_HOLD_MINUTES),
            )
            Payment.objects.create(booking=booking, amount_rub=booking.price_rub)
            queue_booking_event(booking, "reserved")
            return booking
    except (Employee.DoesNotExist, Service.DoesNotExist):
        raise ValidationError(_("This service or employee is no longer listed.")) from None
    except IntegrityError:
        raise ValidationError(_("That request or appointment has already been taken. Please choose again.")) from None


def settle_demo_payment(*, booking_id, customer, outcome, event_key, now=None):
    now = now or timezone.now()
    if not settings.DEMO_PAYMENTS_ENABLED:
        raise ValidationError(_("Demo payments are disabled."))
    if outcome not in {"success", "failure"}:
        raise ValidationError(_("Unknown payment result."))
    initial = Booking.objects.get(pk=booking_id, customer=customer)
    with transaction.atomic():
        employee = Employee.objects.select_for_update().get(pk=initial.employee_id)
        booking = Booking.objects.select_for_update().get(pk=initial.pk, customer=customer)
        payment = Payment.objects.select_for_update().get(booking=booking)
        if booking.status != Booking.Status.HELD:
            return booking
        if booking.hold_expires_at <= now:
            expire_employee_holds(employee, now)
            booking.refresh_from_db()
            return booking
        if payment.amount_rub != booking.price_rub or payment.currency != "RUB":
            raise ValidationError(_("The payment amount does not match this booking."))
        if not employee.is_active:
            raise ValidationError(_("This employee is no longer accepting bookings. Cancel this hold and choose another employee."))
        event, _created = PaymentEvent.objects.get_or_create(key=event_key, defaults={"payment": payment, "outcome": outcome})
        if event.payment_id != payment.pk or event.outcome != outcome:
            raise ValidationError(_("That payment event has already been used."))
        if outcome == "success":
            payment.status, booking.status = Payment.Status.SUCCEEDED, Booking.Status.CONFIRMED
        else:
            payment.status, booking.status = Payment.Status.FAILED, Booking.Status.CANCELLED
            booking.cancelled_at = now
        payment.save(update_fields=["status", "updated_at"])
        booking.save(update_fields=["status", "cancelled_at"])
        # Saving Payment separately can leave a cached reverse relation on Booking.
        booking.payment = payment
        queue_booking_event(booking, "confirmed" if outcome == "success" else "payment_failed")
        return booking


def cancel(*, booking_id, customer=None, staff=False, now=None):
    now = now or timezone.now()
    query = Booking.objects.filter(pk=booking_id)
    if not staff:
        query = query.filter(customer=customer)
    initial = query.get()
    with transaction.atomic():
        Employee.objects.select_for_update().get(pk=initial.employee_id)
        booking = Booking.objects.select_for_update().get(pk=initial.pk)
        payment = Payment.objects.select_for_update().get(booking=booking)
        if booking.status == Booking.Status.CANCELLED:
            return booking
        if booking.status == Booking.Status.EXPIRED or (booking.status == Booking.Status.HELD and booking.hold_expires_at <= now):
            expire_employee_holds(booking.employee, now)
            booking.refresh_from_db()
            return booking
        if booking.status == Booking.Status.CONFIRMED and not staff and booking.starts_at - now < timedelta(hours=2):
            raise ValidationError(_("Cancellation closes two hours before the appointment."))
        if payment.status == Payment.Status.SUCCEEDED:
            payment.status = Payment.Status.REFUNDED
            PaymentEvent.objects.get_or_create(key=f"refund:{booking.pk}", defaults={"payment": payment, "outcome": "full_refund"})
        elif payment.status == Payment.Status.PENDING:
            payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])
        booking.status, booking.cancelled_at = Booking.Status.CANCELLED, now
        booking.save(update_fields=["status", "cancelled_at"])
        booking.payment = payment
        queue_booking_event(booking, "cancelled")
        return booking
