#!/usr/bin/env bash
# Local development only. No packages are installed by this script.
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

if [[ $# -eq 0 ]]; then
  set -- 127.0.0.1:8000
fi
exec .venv/bin/python manage.py runserver "$@"
