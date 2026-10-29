from django import template

from booking.presentation import service_title

register = template.Library()
register.filter("service_title", service_title)
