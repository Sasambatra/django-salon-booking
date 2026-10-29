set -e

.venv/bin/python scripts/check_public_files.py
.venv/bin/python scripts/compile_translations.py --check
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --verbosity 2
