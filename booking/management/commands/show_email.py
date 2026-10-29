from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Read the newest private local-development email. Never sends email."

    def handle(self, **options):
        if not settings.DEBUG or not settings.EMAIL_BACKEND.endswith("filebased.EmailBackend"):
            raise CommandError("This command is only for the local file-based email backend.")
        files = list(Path(settings.EMAIL_FILE_PATH).glob("*.log"))
        if not files:
            raise CommandError("No local email exists yet. Sign up or request password recovery first.")
        from email import policy
        from email.parser import Parser
        raw = max(files, key=lambda path: path.stat().st_mtime).read_text()
        message = Parser(policy=policy.default).parsestr(raw)
        self.stdout.write(f"To: {message['To']}\nSubject: {message['Subject']}\n")
        body = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
        self.stdout.write(body.get_content())
