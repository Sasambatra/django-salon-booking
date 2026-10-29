from django.core.management.base import BaseCommand
from booking.services import expire_due_holds


class Command(BaseCommand):
    help = "Expire unpaid holds. Availability also ignores expired holds automatically."

    def handle(self, **options):
        count = expire_due_holds()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} unpaid hold(s)."))
