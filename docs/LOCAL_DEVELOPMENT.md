# Detailed local development reference

For the project overview and screenshots, start with [README](../README.md).
For a new checkout, use [Setup](SETUP.md). This reference retains the detailed
operational notes from development.

A complete local portfolio booking application built with Django 5.2.18,
PostgreSQL and Django templates. The site uses the existing ivory/green studio
interface and works on mobile, tablet and desktop.

Customers can register, verify their email, recover passwords, enable two-factor
authentication, choose an employee/date/time, place a temporary hold, complete
local demo checkout, view their bookings and cancel eligible appointments.
Administrators manage employees, service details, time off, cancellations and
CSV exports. Staff must verify their email and set up an authenticator before
entering administration. TOTP secrets are encrypted at rest.

Payments/refunds are explicitly simulated: no payment-provider account or real
card data is used. The user selected full demo refunds for eligible cancellations.
Local verification/recovery emails are written to a private project directory;
configure SMTP for actual delivery. Public hosting is not configured by this
local implementation.

## Booking rules

- Moscow time (Europe/Moscow), Monday–Saturday 09:00–18:00; Sunday closed.
- The saved haircut costs 300 RUB and lasts 30 minutes. Available starts use
  a 30-minute grid, up to 60 days ahead. For this service the last start is 17:30.
- Availability is specific to the chosen employee and respects time off.
- Selecting a time creates a ten-minute unpaid hold; expired holds stop blocking
  availability. A later reservation expires stale records before reusing a time.
- Confirmation follows a successful server-side demo-payment result.
- Customer cancellation is allowed at least two hours before a confirmed visit,
  including exactly at the cutoff, with a full simulated refund.
- Repeated booking/payment/cancellation requests do not create duplicate charges
  or refunds. Booking writes lock the employee row inside a transaction and
  check interval overlap; a database uniqueness rule also protects equal starts.
- Prices, names and appointment intervals are saved with the booking so later
  catalog edits preserve agreed details. User/service/employee links are protected.
- All reservation changes use the shared functions in booking/services.py;
  the administrator cannot directly edit booking/payment ledger fields.

## Russian and English

The website chooses Russian or English from the browser's preferred languages.
It uses Russian when neither supported language is requested. The visible
**Русский / English** buttons let visitors override that choice; a protected
cookie remembers it in that browser for one year. Changing language keeps the
current page, query parameters, chosen employee and date. It never submits the
booking or payment form. Location permission and external translation services
are not required. Appointment times remain Moscow time and prices remain RUB.

Django's LocaleMiddleware chooses the language. Templates and Python messages
use Django translation functions, including labels, validation, booking/payment
states and the JavaScript countdown. Django/allauth provide the existing account
and MFA translations; verification and password-recovery emails use the language
of the request that sends them. Email is still delivered to local files unless
SMTP has been configured separately.

The project's 185 Russian messages are in `locale/ru/LC_MESSAGES/django.po`.
English is the original text. The matching `.mo` file is the compiled catalog
that Django reads; both files belong in version control. To edit translations,
change the matching `msgstr` in the `.po` file and, from the project root, run:

```bash
.venv/bin/python scripts/compile_translations.py
```

This uses the existing project Python and only its standard library; it installs
nothing. The helper compiles this catalog's singular message entries and checks
that named placeholders match. Existing Django/allauth catalogs already include
plural translations. Restart the development server after recompiling because
translation catalogs are cached. `scripts/check.sh` checks the compiled file
against its editable source and fails if it is missing or stale.

The sample Haircut appointment title is displayed as Стрижка in Russian. Custom
service names and people's names remain as entered; new service names need an
explicit translation in `booking/presentation.py` and the catalog if both display
versions are wanted. Original booking snapshots are never rewritten to translate
them. Machine translation is not called for every visit.

## Run locally

Use the Ubuntu terminal and the existing project virtual environment. From the
project folder, load your private settings, check Django and start the server:

```bash
set -a
source .env
set +a
.venv/bin/python manage.py check
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

`set -a` exports the assignments read by `source .env` to programs started from
this terminal. `set +a` turns automatic export off afterward without removing
the values already exported. Load the file in each new terminal before running
Django commands. Django itself does not automatically read `.env`.

The check should report `System check identified no issues (0 silenced).`
Stop on an error before starting the server. Leave the server terminal open;
press **Ctrl+C** to stop it. Saved Python changes normally reload automatically.
For changes to `.env`, stop the server, load the file again and restart it.

- [Services](http://127.0.0.1:8000/services/)
- [Haircut details](http://127.0.0.1:8000/services/haircut/)
- [Choose an employee](http://127.0.0.1:8000/services/haircut/employee/)
- [Preferred date](http://127.0.0.1:8000/services/haircut/date/)
- [Opening hours](http://127.0.0.1:8000/opening-hours/)

The root address also displays the service list.
Use the employee page's continuation button to open the date form with the
chosen employee. Opening the date URL without an employee starts at selection.

## Local owner and email access

Initial owner access is saved in **.local/owner-access.txt**, with owner-only
permissions. It contains the generated login email and random password.
The password is never printed into the setup log or chat. Sign in, open
/admin/, reauthenticate if asked and scan the authenticator QR code. Save the
recovery codes privately. The initial example.test address is a reserved local
bootstrap identity, not proof of external mailbox ownership. Before deploying,
create a production owner with a real verified email and deactivate demo access.

On a fresh configured database, run migrate first. setup_demo loads the saved
haircut if missing and creates two sample stylists only when there are none.
Existing service/employee records and administrator passwords are preserved.
The setup command never resets an existing administrator:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_demo
```

After signing up or requesting password recovery locally, read the newest
private development email from the loaded project terminal:

```bash
.venv/bin/python manage.py show_email
```

Open its link in the browser. Local emails and generated credentials are under
.local/, excluded from Git. That folder must remain private.

Useful account/booking addresses:

- /accounts/signup/ and /accounts/login/
- /accounts/password/reset/ and /accounts/profile/
- /accounts/2fa/ for authenticator/recovery-code management
- /bookings/ for the signed-in customer's appointments
- /admin/ for verified staff with authenticator MFA
- /health/ for a minimal database readiness response

## Sample service data

The local database already contains the sample haircut. For a new local
database, apply the saved migrations, then load the fixture from the project
root after loading .env:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py loaddata services
```

This creates the saved tables and loads the 300 RUB, 30-minute haircut with
record ID 1. Loading the fixture again restores that record's sample values;
it can overwrite edits to that record. Tests load their own copy of the
fixture into the separate test database.

## Private settings

`.env.example` documents the setting names and safe example values. `.env`
contains your private key and local values. Git excludes `.env` and permits
`.env.example`. Keep the private key out of screenshots, messages and commits.

| Setting | Meaning |
| --- | --- |
| `DJANGO_SECRET_KEY` | A private random key required by Django. The example placeholder is rejected. |
| `DJANGO_DEBUG` | `true` enables local debug mode; the default is `false`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names; the default is `127.0.0.1,localhost`. |
| `DB_NAME` | The project's PostgreSQL database, currently `room_booking`. |
| `DB_USER` | Its dedicated database login, currently `room_booking`. |
| `DB_PASSWORD` | That login's private database password. |
| `DB_HOST` | The database server address, currently local `127.0.0.1`. |
| `DB_PORT` | The database server port, currently `5432`. |

If `.env` already exists, preserve its key and database values. If it is
missing, generate it once from the project root using the existing Python
installation. The database login/database must already be set up; replace the
generated DB_PASSWORD placeholder privately before running Django commands:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from secrets import token_urlsafe

env_file = Path(".env")
env_file.touch(mode=0o600, exist_ok=False)
env_text = f"""DJANGO_SECRET_KEY={token_urlsafe(64)}
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DB_NAME=room_booking
DB_USER=room_booking
DB_PASSWORD=replace-with-your-database-password
DB_HOST=127.0.0.1
DB_PORT=5432
"""
env_file.write_text(env_text, encoding="utf-8")
print("Created private .env; set its DB_PASSWORD before running Django.")
PY
```

This creates an owner-only file, refuses to overwrite an existing file and does
not display the generated key. It uses Python's built-in `pathlib` and `secrets`
modules. No dependency is installed by these commands.

## Project files

- `config/settings.py` reads the environment and configures PostgreSQL, templates and CSS.
- `config/urls.py` connects web addresses to view functions.
- `config/views.py` supplies catalog/availability pages; `booking/views.py` handles owned bookings, checkout, cancellation and accounts.
- `config/forms.py` validates dates, employee choices and reservation inputs.
- `booking/__init__.py` makes the booking folder a regular Python package.
- `booking/apps.py` describes the app activated in Django's settings.
- `booking/models.py` defines services, employees, immutable booking snapshots, demo payments/events, time off and shared security state.
- `booking/services.py` owns availability, reservation, expiry, demo settlement and refund rules.
- `booking/admin.py` provides protected staff management and CSV export.
- `booking/security.py` validates trusted proxy identity and enforces request limits and private response headers.
- `booking/adapters.py` encrypts authenticator secrets using the project key.
- `booking/presentation.py` and `booking/templatetags/booking_text.py` translate the sample service title for display while preserving stored names.
- `locale/ru/LC_MESSAGES/django.po` holds editable Russian translations; `django.mo` is the matching compiled catalog.
- `scripts/compile_translations.py` compiles/checks the project catalog without additional software.
- `booking/fixtures/services.json` supplies the sample haircut for setup and page tests.
- `booking/migrations/0001_initial.py` describes the service table and duration rule.
- `booking/migrations/0002_employee.py` describes the employee table.
- `booking/migrations/__init__.py` marks the migration package for Django.
- `booking/tests.py` checks service validation/storage; `booking/test_workflow.py` covers the full booking, account, payment, security and concurrency workflows.
- `booking/test_languages.py` checks language selection, remembered choices, safe switching, translated booking/refunds and account emails.
- `templates/booking/base.html` holds the shared page structure and stylesheet.
- `templates/booking/service_list.html` displays the service list or its empty message.
- `templates/booking/service_card.html` displays the service values.
- `templates/booking/choose_date.html` displays the date form and its result.
- `templates/booking/choose_employee.html` displays active employee choices and the date continuation link.
- `templates/booking/opening_hours.html` displays the opening hours.
- `static/booking/service-card.css` styles all Django pages.
- `config/tests.py` checks pages, date input, employee choices and the practice booking rules.
- `scripts/check.sh` checks configuration, missing migrations and all project tests.
- `exercises/` contains the earlier standalone learning examples.

## Python practice checks

From the project root in a separate terminal:

```bash
.venv/bin/python exercises/service_data.py
.venv/bin/python exercises/type_check_demo.py
```

The first prints the service details and the gap results `False`, `True`,
`True`. The second prints those three results on separate lines.

## Project checks

From the project root, after loading `.env` as shown in Run locally:

```bash
bash scripts/check.sh
```

This checks translation catalogs and Django's configuration, verifies that model
changes have migration files and runs all project tests. The script stops if a
command fails.
Database tests create a separate PostgreSQL database named test_room_booking,
apply migrations there, and discard it afterward. The local development role
has CREATEDB permission for that workflow; it is not a database superuser.
The tests do not populate the regular room_booking database. The configuration,
migration checks and all **136 saved tests** passed against PostgreSQL.
Coverage includes actual concurrent PostgreSQL transactions, overlapping starts,
replayed payments, exact cancellation boundaries, full refunds, expired holds,
CSRF, customer ownership, encrypted MFA secrets, staff access, rate limits,
proxy-header trust and CSV formula handling. Eighteen language tests also cover
regional browser preferences, Russian fallback, remembered overrides, safe
redirects and CSRF, translated date errors, checkout/expiry/refund text, Russian
and English emails, escaped catalog names and compiled-catalog consistency.
Thirty-four Telegram tests use fake transport to check allowlisting, transactional
queueing, retries, revocation, command processing and the website-to-bot event flow.

Live Chrome verification passed registration, file-email verification, password
recovery, successful/failed checkout, receipt/refund, employee-specific live
availability, staff MFA setup and subsequent MFA login, time off and CSV export.
Forty account/booking page checks passed at widths 320/375/768/1280. Temporary
browser-test identities/records were removed afterward. Private verification
metadata is in .local/reports/verification.json.

Russian/English verification additionally passed 112 page checks at the same
four widths, with no horizontal overflow or JavaScript errors. Keyboard language
switching preserved the employee/date and remained selected on another page.
Live demo checkout in English, switching the receipt to Russian and completing
a full refund passed. The tagged customer, booking, payment and session were
removed afterward. Results are in .local/reports/language-verification.json.

With explicit user approval, Psycopg 3.3.6 and its matching binary component
were installed in the project virtual environment; requirements.txt records
psycopg[binary]==3.3.6. Django uses this connector for its PostgreSQL requests.

With explicit approval, django-allauth[mfa] 65.20.1 and its required dependencies
were installed into the existing .venv. Django and the existing connector were
not upgraded. Exact dependency versions are recorded in requirements.txt.

## Operations and recovery

From the project terminal after loading .env:

```bash
.venv/bin/python manage.py expire_holds
.venv/bin/python manage.py backup_database
```

The first reconciles expired unpaid holds. The second uses the existing pg_dump
client to create a private custom-format backup in .local/backups/. Keep the
project SECRET_KEY securely with your recovery configuration: encrypted MFA
secrets need that key. The pre-build backup and a completed-site backup are
saved locally. The completed-site backup was restored and checked in a separate
temporary PostgreSQL database, which was then removed; the application database
was not replaced.

To restore, stop app writes, create a separate database owned by the project
role, and restore with pg_restore. Verify that copy before selecting it through
DB_NAME. Avoid overwriting the live database during a recovery rehearsal.

## Security and deployment boundaries

The application includes verified-email gates, maintained Django/allauth auth,
mandatory staff authenticator MFA, encrypted MFA secrets, password validation,
CSRF, owner-scoped booking lookups, signed demo-result tokens, transactional
booking/payment changes, per-identity request limits (30 POST/300 GET per minute),
allauth login/signup/recovery throttles, private response caching rules, CSP on
site/account pages, clickjacking protection and bounded request bodies.

Proxy headers are accepted only from configured TRUSTED_PROXY_CIDRS. Leave this
empty for direct local access. A production reverse proxy must overwrite
X-Real-IP/X-Forwarded-Proto and the origin must be restricted to that proxy.

Production settings enable secure cookies, HTTPS redirects and HSTS when
DJANGO_DEBUG=false, and require SMTP settings with a real sender. Domain-wide
HSTS subdomains/preload remain explicit opt-ins because the deployment domain
and its other subdomains have not been provided. The production-profile check
reported only those two domain-wide HSTS recommendations with sample SMTP/host
configuration; SMTP delivery and external TLS were not tested.

Before a public launch, configure real SMTP credentials, the domain, TLS, a
production WSGI/ASGI server, static-file serving, edge/firewall protection,
trusted proxy ranges, backup retention and monitoring. Use a separate least-privilege
production database role; the local development role keeps CREATEDB for test databases. Django runserver is for
local development. Those external controls are not deployed protections.
A live payment provider and Telegram/Sheets integrations are not part of this
approved local payment simulator. Replacing the simulator requires actual
merchant eligibility, credentials and verified provider callbacks. New software
installations continue to require explicit user approval.
