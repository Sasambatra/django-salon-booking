# Set up a new checkout

[Русский](SETUP.ru.md) · **English**

These are manual instructions for a new Ubuntu/WSL checkout. The prepared
development copy already has its environment and does not need reinstalling.
All commands start in the repository root, the folder containing manage.py.

Clone the public source, then enter the new folder:

```bash
git clone https://github.com/Sasambatra/django-salon-booking.git
cd django-salon-booking
```

The clone contains source and example settings only; it does not include a
database, private credentials or an installed virtual environment.

## 1. Prerequisites

The verified local stack is Python 3.14, PostgreSQL 18, Git and Bash. Python
needs its venv module; PostgreSQL needs the psql client and a running server.
pg_dump/pg_restore are used for the optional backup workflow.

The project does not install these tools automatically. It has no Node/npm build.

## 2. Python dependencies

On a new machine, create a project-only environment and install the exact package
versions recorded in requirements.txt:

    python3.14 -m venv .venv
    .venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
    .venv/bin/python -m pip check

The first command creates the isolated .venv folder. The next installs the pinned
dependencies into that folder, with wheels rather than source builds.
pip check verifies that their requirements agree.

## 3. Private settings

    .venv/bin/python scripts/init_env.py

This copies the safe .env.example settings to an owner-only .env, replaces the
example Django key with a new random key and never overwrites an existing file.
It does not print the key. Open .env in your editor and set DB_PASSWORD privately.
The example defaults to local development and file-based email.

.env uses Bash assignments because the local scripts source it. Quote passwords
that contain shell-special characters. Keep the file out of screenshots and Git.
The repository includes .env.example to describe settings, not real credentials.

## 4. PostgreSQL

Use a dedicated database and role. On a fresh Ubuntu PostgreSQL installation,
open its administrator console:

    sudo -u postgres psql

In that console, create the local role/database and set its password interactively:

    CREATE ROLE room_booking WITH LOGIN CREATEDB;
    \password room_booking
    CREATE DATABASE room_booking OWNER room_booking;
    \q

The role can create the temporary test database but is not a database superuser.
Use the same role/password/database values in your private .env. These commands
are for initial creation; preserve an existing configured role and database.
Other operating systems can perform the equivalent setup in their PostgreSQL
administrator tool.

## 5. Migrations and sample records

Load the private settings into the current terminal:

    set -a
    source .env
    set +a

set -a exports assignments read by source; set +a turns automatic export off
afterward. Then create the schema and initial local data:

    .venv/bin/python manage.py migrate
    .venv/bin/python manage.py setup_demo

Migrations create the application and account tables. setup_demo adds the sample
haircut if it is missing, two stylists if there are none, and initial owner
access if no administrator exists. It preserves existing administrator passwords
and existing catalog/staff records.

Read .local/owner-access.txt privately. Its generated password is never printed
by setup_demo. The local bootstrap owner is a reserved example.test identity;
first administration access requires an authenticator and password confirmation.
No shared public administrator password is provided.

## 6. Checks and server

    bash scripts/check.sh
    bash scripts/run.sh

The checks stop at the first error and use a separate PostgreSQL test database.
run.sh reads .env itself and uses the existing environment; it installs nothing.

Open [services](http://127.0.0.1:8000/services/) and
[About the studio](http://127.0.0.1:8000/about/).
Leave the server terminal open; Ctrl+C stops it.
To choose a different local port:

    bash scripts/run.sh 127.0.0.1:8001

Saved Python code normally reloads. Restart after changing .env or recompiling
translations. If port 8000 is already serving this application, use that instance.

## Local email and language

After registering or requesting a password reset:

    .venv/bin/python manage.py show_email

This displays the newest locally saved message in your private terminal.
Follow its link in the same browser. It does not send an external email.

Russian/English follows the browser's preference; the visible language buttons
remember a choice for one year. Russian is the fallback. Appointment times stay
in Moscow and amounts stay RUB. Edit the Russian PO catalog and run:

    .venv/bin/python scripts/compile_translations.py

The existing Python compiles the project messages without additional packages.
Restart the server afterward. Original booking snapshots and custom names are
preserved. New custom service titles need explicit presentation/catalog entries.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing DJANGO_SECRET_KEY or DB setting | Load .env in that terminal; preserve its real values |
| PostgreSQL connection refused | Confirm the server is running and DB_HOST/DB_PORT agree |
| Password authentication failed | Compare the private DB_PASSWORD with the dedicated role |
| Permission denied to create test database | The development/test role needs CREATEDB |
| No employees available | Run setup_demo against the intended local database |
| Empty owner access file or unknown password | Preserve existing access; setup_demo never resets an administrator |
| Translation checker reports a stale catalog | Recompile the PO catalog and restart the server |

For backup/restore and detailed settings, see
[the local reference](LOCAL_DEVELOPMENT.md).
