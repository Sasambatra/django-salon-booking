import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Service(models.Model):
    name = models.CharField(max_length=100)
    price_rub = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(price_rub__gt=0), name="service_price_positive"),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="service_duration_positive",
            ),
        ]


class Employee(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Booking(models.Model):
    class Status(models.TextChoices):
        HELD = "held", _("Awaiting demo payment")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Payment time expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    service_name = models.CharField(max_length=100)
    employee_name = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    price_rub = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.HELD)
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [models.Index(fields=["employee", "starts_at", "ends_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="booking_end_after_start"),
            models.CheckConstraint(condition=Q(price_rub__gt=0), name="booking_price_positive"),
            models.CheckConstraint(condition=~Q(status="held") | Q(hold_expires_at__isnull=False), name="held_booking_has_expiry"),
            models.UniqueConstraint(fields=["employee", "starts_at"], condition=Q(status__in=["held", "confirmed"]), name="one_active_employee_start"),
        ]

    def __str__(self):
        local_start = timezone.localtime(self.starts_at)
        return f"{self.service_name} · {self.employee_name} · {local_start:%Y-%m-%d %H:%M} Moscow"

    @property
    def can_cancel(self):
        if self.status == self.Status.HELD:
            return self.hold_is_live
        return self.status == self.Status.CONFIRMED and self.starts_at - timezone.now() >= timedelta(hours=2)

    @property
    def hold_is_live(self):
        return self.status == self.Status.HELD and self.hold_expires_at and self.hold_expires_at > timezone.now()


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Awaiting demo payment")
        SUCCEEDED = "succeeded", _("Demo payment successful")
        FAILED = "failed", _("Demo payment failed")
        REFUNDED = "refunded", _("Full demo refund completed")
        EXPIRED = "expired", _("Payment time expired")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="payment")
    amount_rub = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB", editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_rub__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"Demo {self.amount_rub} {self.currency} · {self.get_status_display()}"


class PaymentEvent(models.Model):
    key = models.CharField(max_length=100, unique=True)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="events")
    outcome = models.CharField(max_length=20)
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class TimeOff(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="time_off")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=120, default="Unavailable")

    class Meta:
        ordering = ["starts_at"]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="time_off_end_after_start")]

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(_("The end must be after the start."))
        if self.employee_id and self.starts_at and self.ends_at:
            occupied = Booking.objects.filter(employee_id=self.employee_id, starts_at__lt=self.ends_at, ends_at__gt=self.starts_at).filter(
                Q(status=Booking.Status.CONFIRMED) | Q(status=Booking.Status.HELD, hold_expires_at__gt=timezone.now())
            )
            if occupied.exists():
                raise ValidationError(_("Move or cancel the overlapping appointment before adding time off."))

    def __str__(self):
        return f"{self.employee} · {self.reason}"


class SecurityCache(models.Model):
    cache_key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    expires = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "booking_security_cache"


class RequestThrottle(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window_start = models.DateTimeField(default=timezone.now)
    count = models.PositiveIntegerField(default=0)


class TelegramDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        REVOKED = "revoked", "Recipient access removed"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="telegram_deliveries")
    bot_id = models.PositiveBigIntegerField()
    chat_id = models.PositiveBigIntegerField()
    event = models.CharField(max_length=20)
    text = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="telegram_delivery_due")]
        constraints = [models.UniqueConstraint(
            fields=["booking", "bot_id", "chat_id", "event"], name="telegram_event_recipient_once",
        )]


class TelegramCursor(models.Model):
    # One cursor per bot, so changing to a different bot cannot reuse its offset.
    bot_id = models.PositiveBigIntegerField(primary_key=True)
    next_update_id = models.PositiveBigIntegerField(default=0)
    delivery_not_before = models.DateTimeField(default=timezone.now)
