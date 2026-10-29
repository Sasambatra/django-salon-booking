from django.conf import settings


def site_context(request):
    return {"demo_payments": settings.DEMO_PAYMENTS_ENABLED, "local_email": settings.DEBUG and settings.EMAIL_BACKEND.endswith("filebased.EmailBackend"), "is_account": request.path.startswith("/accounts/")}
