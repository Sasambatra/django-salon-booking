from django import forms
from django.utils.translation import gettext_lazy as _

from booking.models import Employee
from booking.services import validate_day


class AppointmentDateForm(forms.Form):
    date = forms.DateField(
        label=_("Preferred date"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def clean_date(self):
        day = self.cleaned_data["date"]
        validate_day(day)
        return day


class EmployeeChoiceForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(is_active=True).order_by("pk"),
        label=_("Employee"),
        empty_label=_("Choose an employee"),
    )


class ReserveForm(AppointmentDateForm):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True), widget=forms.HiddenInput)
    time = forms.TimeField(input_formats=["%H:%M"], widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)
