"""Create a private local configuration without overwriting an existing .env."""
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent.parent


def create_env(directory):
    directory = Path(directory)
    example = (directory / ".env.example").read_text(encoding="utf-8")
    placeholder = "DJANGO_SECRET_KEY=generate-your-own-key"
    if placeholder not in example:
        raise ValueError("The example configuration is missing its secret-key placeholder.")
    content = example.replace(
        placeholder, "DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64), 1
    )
    destination = directory / ".env"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return destination


def main():
    try:
        create_env(ROOT)
    except FileExistsError:
        print("Existing .env preserved. No settings or credentials were changed.")
    else:
        print("Created private .env. Set DB_PASSWORD privately before starting Django.")


if __name__ == "__main__":
    main()
