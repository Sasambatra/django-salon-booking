import hashlib
from ipaddress import ip_address, ip_network
from datetime import timedelta

from django.db import transaction
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _

from booking.models import RequestThrottle


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = [ip_network(item) for item in settings.TRUSTED_PROXY_CIDRS]

    def __call__(self, request):
        try:
            peer = ip_address(request.META.get("REMOTE_ADDR", ""))
            trusted = any(peer in network for network in self.networks)
        except ValueError:
            trusted = False
        if trusted:
            try:
                request.META["REMOTE_ADDR"] = str(ip_address(request.META.get("HTTP_X_REAL_IP", "")))
            except ValueError:
                pass
            if request.META.get("HTTP_X_FORWARDED_PROTO") not in {"http", "https"}:
                request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        else:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        return self.get_response(request)


class SecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in {"GET", "POST"} and not request.path.startswith("/admin/") and request.path != "/health/":
            identity = f"user:{request.user.pk}" if request.user.is_authenticated else f'ip:{request.META.get("REMOTE_ADDR", "unknown")}'
            key = hashlib.sha256(f"{request.method}:{identity}".encode()).hexdigest()
            with transaction.atomic():
                RequestThrottle.objects.get_or_create(key=key)
                bucket = RequestThrottle.objects.select_for_update().get(key=key)
                now = timezone.now()
                if now - bucket.window_start >= timedelta(minutes=1):
                    bucket.window_start, bucket.count = now, 0
                bucket.count += 1
                bucket.save(update_fields=["window_start", "count"])
                limit = 30 if request.method == "POST" else 300
                if bucket.count > limit:
                    response = HttpResponse(_("Too many requests. Please wait one minute and try again."), status=429)
                    response["Retry-After"] = "60"
                    return response
        if request.path.startswith("/admin/") and request.user.is_authenticated and request.user.is_staff:
            from allauth.account.models import EmailAddress
            from allauth.mfa.models import Authenticator
            if not EmailAddress.objects.filter(user=request.user, verified=True).exists():
                return redirect("account_email")
            if not Authenticator.objects.filter(user=request.user, type="totp").exists():
                return redirect("mfa_activate_totp")
        response = self.get_response(request)
        if request.path.startswith(("/accounts/", "/bookings/", "/admin/")):
            response["Cache-Control"] = "private, no-store"
        if not request.path.startswith("/admin/"):
            response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        response["Referrer-Policy"] = "same-origin"
        return response
