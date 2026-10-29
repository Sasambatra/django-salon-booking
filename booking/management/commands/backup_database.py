import os
import secrets
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create an owner-only PostgreSQL backup using the existing pg_dump client."

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path)

    def handle(self, **options):
        path = options.get("output") or settings.BASE_DIR / ".local" / "backups" / f"studio-{timezone.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.dump"
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        database = settings.DATABASES["default"]
        environment = dict(os.environ, PGPASSWORD=database["PASSWORD"])
        command = ["pg_dump", "--host", database["HOST"], "--port", str(database["PORT"]), "--username", database["USER"], "--format=custom", "--file", str(path), database["NAME"]]
        try:
            subprocess.run(command, env=environment, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as error:
            path.unlink(missing_ok=True)
            raise CommandError("The database backup failed. Check the local PostgreSQL client and connection.") from error
        self.stdout.write(f"Backup saved privately: {path}")
