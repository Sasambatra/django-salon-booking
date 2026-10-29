"""Review files offered to Git without printing any matched credential value."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PARTS = {".local", ".venv", "__pycache__", "node_modules"}
PRIVATE_SUFFIXES = {".pyc", ".dump", ".pem", ".key"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "Telegram bot token": re.compile(r"\b[0-9]{6,}:[A-Za-z0-9_-]{30,}\b"),
    "credential URL": re.compile(r"(?:postgres(?:ql)?|https?)://[^\s:/]+:[^\s@]+@"),
}


def find_issues(directory):
    directory = Path(directory)
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=directory,
    ).decode().split("\0")
    issues = []
    for name in sorted(set(filter(None, names))):
        path = Path(name)
        if (
            PRIVATE_PARTS.intersection(path.parts)
            or path.suffix in PRIVATE_SUFFIXES
            or (path.name.startswith(".env") and path.name != ".env.example")
        ):
            issues.append(f"{name}: private local file offered to Git")
            continue
        full_path = directory / path
        if not full_path.is_file():
            continue
        if not full_path.resolve().is_relative_to(directory.resolve()):
            issues.append(f"{name}: link points outside the checkout")
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                issues.append(f"{name}:{line}: possible {label} (value suppressed)")
    return issues


def main():
    issues = find_issues(ROOT)
    if issues:
        print("\n".join(issues))
        raise SystemExit(1)
    print("Public-file review passed: no private paths or supported credential patterns found.")


if __name__ == "__main__":
    main()
