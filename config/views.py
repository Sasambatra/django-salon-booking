import uuid
from datetime import timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from booking.models import Service
from config.forms import AppointmentDateForm, EmployeeChoiceForm
from booking.services import available_starts


HAIRCUT_ID = 1


def service_list(request):
    services = Service.objects.order_by("pk")
    return render(request, "booking/service_list.html", {"services": services})


def service_card(request):
    service = get_object_or_404(Service, pk=HAIRCUT_ID)
    return render(request, "booking/service_card.html", {"service": service})


def opening_hours(request):
    return render(request, "booking/opening_hours.html")


def about_studio(request):
    return render(request, "booking/about_studio.html")


def choose_employee(request):
    service = get_object_or_404(Service, pk=HAIRCUT_ID)
    form = EmployeeChoiceForm(request.GET or None)
    selected_employee = None
    if form.is_valid():
        selected_employee = form.cleaned_data["employee"]
    return render(
        request,
        "booking/choose_employee.html",
        {
            "form": form,
            "selected_employee": selected_employee,
            "has_employees": form.fields["employee"].queryset.exists(),
            "service": service,
        },
    )


def choose_date(request):
    service = get_object_or_404(Service, pk=HAIRCUT_ID)
    employee_form = EmployeeChoiceForm(request.GET)
    if not employee_form.is_valid():
        return redirect("choose-employee")
    employee = employee_form.cleaned_data["employee"]
    form = AppointmentDateForm(request.GET if "date" in request.GET else None)
    selected_date = None
    starts = []
    today = timezone.localdate()
    form.fields["date"].widget.attrs.update({"min": today.isoformat(), "max": (today + timedelta(days=settings.BOOKING_ADVANCE_DAYS)).isoformat()})
    if form.is_valid():
        selected_date = form.cleaned_data["date"]
        starts = available_starts(service, employee, selected_date)
    return render(
        request,
        "booking/choose_date.html",
        {
            "form": form,
            "selected_date": selected_date,
            "employee": employee,
            "service": service,
            "starts": starts,
            "request_key": uuid.uuid4(),
        },
    )
