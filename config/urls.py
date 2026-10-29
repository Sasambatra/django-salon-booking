"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from allauth.account.decorators import secure_admin_login
from booking import views as booking_views
from config.views import (
    about_studio,
    choose_date,
    choose_employee,
    opening_hours,
    service_card,
    service_list,
)

admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("about/", about_studio, name="about-studio"),
    path("i18n/setlang/", booking_views.set_site_language, name="set_language"),
    path("health/", booking_views.health, name="health"),
    path("admin/", admin.site.urls),
    path("accounts/profile/", booking_views.profile, name="profile"),
    path("accounts/", include("allauth.urls")),
    path("bookings/", booking_views.my_bookings, name="my-bookings"),
    path("bookings/new/", booking_views.create_booking, name="booking-create"),
    path("bookings/<uuid:booking_id>/", booking_views.booking_detail, name="booking-detail"),
    path("bookings/<uuid:booking_id>/checkout/", booking_views.checkout, name="booking-checkout"),
    path("bookings/<uuid:booking_id>/cancel/", booking_views.cancel_booking, name="booking-cancel"),
    path("", service_list, name="home"),
    path("services/", service_list, name="service-list"),
    path("services/haircut/", service_card, name="service-card"),
    path("services/haircut/employee/", choose_employee, name="choose-employee"),
    path("services/haircut/date/", choose_date, name="choose-date"),
    path("opening-hours/", opening_hours, name="opening-hours"),
]
