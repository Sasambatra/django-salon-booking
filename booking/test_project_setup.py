"""Setup helpers must preserve private configuration and avoid publishing it."""
from pathlib import Path
import stat
import subprocess
import tempfile

from django.test import SimpleTestCase

from scripts.check_public_files import find_issues
from scripts.init_env import create_env


class PrivateSetupTests(SimpleTestCase):
    def test_generated_environment_is_private_and_has_a_unique_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text(
                "DJANGO_SECRET_KEY=generate-your-own-key\nDB_PASSWORD=choose-privately\n"
            )
            path = create_env(root)
            content = path.read_text()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("generate-your-own-key", content)
            self.assertIn("DB_PASSWORD=choose-privately", content)
            self.assertGreater(len(content.splitlines()[0].split("=", 1)[1]), 60)

    def test_existing_environment_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text("DJANGO_SECRET_KEY=generate-your-own-key\n")
            existing = root / ".env"
            existing.write_text("preserve-existing-configuration\n")
            with self.assertRaises(FileExistsError):
                create_env(root)
            self.assertEqual(existing.read_text(), "preserve-existing-configuration\n")

    def test_public_review_finds_force_added_private_file_without_its_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", directory], check=True)
            (root / ".gitignore").write_text(".env\n")
            (root / ".env").write_text("PRIVATE_VALUE=never-show-this-value\n")
            self.assertEqual(find_issues(root), [])
            subprocess.run(["git", "add", "--force", ".env"], cwd=root, check=True)
            issues = find_issues(root)
            self.assertEqual(len(issues), 1)
            self.assertIn(".env:", issues[0])
            self.assertNotIn("never-show-this-value", "\n".join(issues))

    def test_public_review_redacts_credential_pattern_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", directory], check=True)
            secret = "ghp_" + "A" * 36
            (root / "example.txt").write_text("line one\n" + secret + "\n")
            issues = find_issues(root)
            self.assertEqual(len(issues), 1)
            self.assertIn("example.txt:2:", issues[0])
            self.assertNotIn(secret, "\n".join(issues))

    def test_public_review_redacts_telegram_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", directory], check=True)
            secret = str(123456789) + ":" + "X" * 35
            (root / "example.txt").write_text("token=" + secret + "\n")
            issues = find_issues(root)
            self.assertEqual(len(issues), 1)
            self.assertIn("Telegram bot token", issues[0])
            self.assertNotIn(secret, "\n".join(issues))
