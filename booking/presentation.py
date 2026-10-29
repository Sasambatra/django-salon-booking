"""Translated display text; stored booking details retain their original values."""
from django.utils.translation import gettext as _


def service_title(name):
    # Custom catalog names and people's names are displayed exactly as entered.
    if name == "Haircut appointment":
        return _("Haircut appointment")
    return name
