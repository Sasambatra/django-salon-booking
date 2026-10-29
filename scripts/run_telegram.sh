#!/usr/bin/env bash
# Uses the existing environment and never installs software.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"
if [[ ! -f .env || ! -x .venv/bin/python ]]; then
  echo "Complete docs/SETUP.md first: .env and .venv/bin/python are required." >&2
  exit 1
fi
set -a
source .env
set +a
exec .venv/bin/python manage.py run_telegram_bot "$@"
