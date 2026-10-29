import uuid
from datetime import datetime
from urllib.parse import urlencode

from allauth.account.decorators import verified_email_required
from django.conf import settings
from django.contrib import messages
from django.db import connection, DatabaseError
from django.http import HttpResponseBadRequest, JsonResponse
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST
from django.views.i18n import set_language

from booking.models import Booking, Payment
from booking.services import cancel, effective_status, reserve, salon_zone, settle_demo_payment
from config.forms import ReserveForm

PAYMENT_SALT = "booking.local-demo-payment.v1"


@require_POST
def set_site_language(request):
    if request.POST.get("language") not in dict(settings.LANGUAGES):
        return HttpResponseBadRequest(_("Choose Russian or English."))
    return set_language(request)


@verified_email_required
@require_POST
def create_booking(request):
    form = ReserveForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            booking = reserve(
                customer=request.user, service_id=1, employee_id=data["employee"].pk,
                starts_at=datetime.combine(data["date"], data["time"], tzinfo=salon_zone()),
                request_key=data["request_key"],
            )
            return redirect("booking-checkout", booking_id=booking.pk)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        return redirect(reverse("choose-date") + "?" + urlencode({"employee": data["employee"].pk, "date": data["date"].isoformat()}))
    messages.error(request, _("That appointment request is invalid or out of date. Please choose again."))
    return redirect("choose-employee")


def owned_booking(request, booking_id):
    return get_object_or_404(Booking.objects.select_related("payment", "employee", "service"), pk=booking_id, customer=request.user)


@verified_email_required
def my_bookings(request):
    bookings = list(Booking.objects.filter(customer=request.user).select_related("payment"))
    for booking in bookings:
        booking.display_status = dict(Booking.Status.choices)[effective_status(booking)]
    return render(request, "booking/my_bookings.html", {"bookings": bookings})


@verified_email_required
def booking_detail(request, booking_id):
    booking = owned_booking(request, booking_id)
    state = effective_status(booking)
    payment_display = Payment.Status.EXPIRED.label if state == Booking.Status.EXPIRED and booking.payment.status == Payment.Status.PENDING else booking.payment.get_status_display()
    return render(request, "booking/booking_detail.html", {"booking": booking, "display_status": dict(Booking.Status.choices)[state], "status_code": state, "payment_display": payment_display})


@verified_email_required
@require_http_methods(["GET", "POST"])
def checkout(request, booking_id):
    booking = owned_booking(request, booking_id)
    if request.method == "POST":
        try:
            action = signing.loads(request.POST.get("action_token", ""), salt=PAYMENT_SALT, max_age=86400)
            if action.get("payment") != str(booking.payment.pk):
                raise signing.BadSignature()
            uuid.UUID(action["event"])
            settle_demo_payment(booking_id=booking.pk, customer=request.user, outcome=action["outcome"], event_key=action["event"])
            return redirect("booking-detail", booking_id=booking.pk)
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            messages.error(request, _("This checkout action is invalid. Refresh the page and try again."))
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        return redirect("booking-checkout", booking_id=booking.pk)
    if not booking.hold_is_live:
        return redirect("booking-detail", booking_id=booking.pk)
    tokens = {outcome: signing.dumps({"payment": str(booking.payment.pk), "outcome": outcome, "event": str(uuid.uuid4())}, salt=PAYMENT_SALT) for outcome in ("success", "failure")}
    return render(request, "booking/checkout.html", {"booking": booking, "success_token": tokens["success"], "failure_token": tokens["failure"], "demo_enabled": settings.DEMO_PAYMENTS_ENABLED})


@verified_email_required
@require_http_methods(["GET", "POST"])
def cancel_booking(request, booking_id):
    booking = owned_booking(request, booking_id)
    if request.method == "POST":
        try:
            result = cancel(booking_id=booking.pk, customer=request.user)
            if result.status == Booking.Status.CANCELLED:
                messages.success(request, _("Your booking is cancelled. Any successful demo payment has been fully refunded."))
            else:
                messages.info(request, _("This payment hold has expired. No demo payment was taken."))
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        return redirect("booking-detail", booking_id=booking.pk)
    return render(request, "booking/cancel_booking.html", {"booking": booking})


@verified_email_required
def profile(request):
    return render(request, "booking/profile.html")


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
