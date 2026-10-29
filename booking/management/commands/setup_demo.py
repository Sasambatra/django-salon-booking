import os
import secrets

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from booking.models import Employee, Service


class Command(BaseCommand):
    help = "Create initial local-demo owner access, without resetting any existing administrator."

    def handle(self, **options):
        if not settings.DEBUG:
            raise CommandError("Local demo setup requires DEBUG=true. Use createsuperuser for a deployed site.")
        if not Service.objects.filter(pk=1).exists():
            call_command("loaddata", "services", verbosity=0)
        if not Employee.objects.exists():
            Employee.objects.create(name="Stylist 1")
            Employee.objects.create(name="Stylist 2")
        user_model = get_user_model()
        if user_model.objects.filter(is_superuser=True).exists():
            self.stdout.write("An administrator already exists; existing credentials were preserved.")
            return
        directory = settings.BASE_DIR / ".local"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / "owner-access.txt"
        password = secrets.token_urlsafe(30)
        with transaction.atomic():
            user = user_model.objects.create_superuser(username="studio_owner", email="owner@studio.example.test", password=password)
            EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(f"LOCAL PORTFOLIO DEMO OWNER\n\nEmail: {user.email}\nPassword: {password}\n\nSign in: http://127.0.0.1:8000/accounts/login/\nAdministration: http://127.0.0.1:8000/admin/\n\nUse an authenticator to complete two-factor setup at your first admin visit.\nThis reserved example.test address is for local owner bootstrap only.\nConfigure SMTP and your own verified address before deployment.\nKeep this file private and remove it after storing the password securely.\n")
        self.stdout.write("Local owner created. Read .local/owner-access.txt privately; the password is not printed.")
