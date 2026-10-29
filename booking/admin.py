import csv

from django.utils.translation import gettext_lazy as _

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse

from booking.models import Booking, Employee, Payment, PaymentEvent, Service, TelegramDelivery, TimeOff
from booking.services import cancel


admin.site.site_header = _("Haircut Studio · Demo administration")
admin.site.site_title = _("Studio administration")
admin.site.index_title = _("Appointments, staff and availability")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "price_rub", "duration_minutes")

    def has_add_permission(self, request):
        return not Service.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


class TimeOffForm(forms.ModelForm):
    class Meta:
        model = TimeOff
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        if employee:
            ids = {employee.pk}
            if self.instance.pk:
                ids.add(TimeOff.objects.get(pk=self.instance.pk).employee_id)
            list(Employee.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
        return cleaned


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    form = TimeOffForm
    list_display = ("employee", "starts_at", "ends_at", "reason")
    list_filter = ("employee",)
    date_hierarchy = "starts_at"


def csv_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "employee_name", "starts_at", "status", "price_rub")
    list_filter = ("status", "employee")
    search_fields = ("customer__email", "service_name", "employee_name")
    readonly_fields = tuple(field.name for field in Booking._meta.fields)
    actions = ("cancel_selected", "export_csv")
    date_hierarchy = "starts_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description=_("Cancel selected bookings and refund demo payments"))
    def cancel_selected(self, request, queryset):
        for booking in queryset:
            try:
                cancel(booking_id=booking.pk, staff=True)
            except ValidationError as error:
                self.message_user(request, " ".join(error.messages), messages.ERROR)
        self.message_user(request, _("Selected bookings processed. Successful demo payments were refunded."))

    @admin.action(description=_("Export selected bookings as CSV"))
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="studio-bookings.csv"'
        response["Cache-Control"] = "private, no-store"
        writer = csv.writer(response)
        writer.writerow(["Reference", "Customer email", "Service", "Employee", "Start", "End", "Price RUB", "Status"])
        for booking in queryset.select_related("customer"):
            writer.writerow([csv_cell(value) for value in (booking.pk, booking.customer.email, booking.service_name, booking.employee_name, booking.starts_at.isoformat(), booking.ends_at.isoformat(), booking.price_rub, booking.status)])
        return response


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "amount_rub", "currency", "status")
    list_filter = ("status",)
    readonly_fields = tuple(field.name for field in Payment._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ("key", "payment", "outcome", "created_at")
    readonly_fields = tuple(field.name for field in PaymentEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TelegramDelivery)
class TelegramDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "event", "chat_id", "status", "attempts", "next_attempt_at")
    list_filter = ("status", "event")
    readonly_fields = tuple(field.name for field in TelegramDelivery._meta.fields)
    actions = ("retry_failed",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Retry failed Telegram deliveries")
    def retry_failed(self, request, queryset):
        from django.utils import timezone

        count = queryset.filter(status=TelegramDelivery.Status.FAILED).update(
            status=TelegramDelivery.Status.PENDING, attempts=0,
            next_attempt_at=timezone.now(), last_error="",
        )
        self.message_user(request, f"Queued {count} failed Telegram delivery(s) for retry.")
