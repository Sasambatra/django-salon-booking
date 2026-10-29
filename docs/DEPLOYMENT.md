# Deployment preparation

The repository is prepared for local portfolio review. It has no public host,
domain, real SMTP delivery or live payment provider. The development server is
not a public production server.

## Configuration still needed for a public demo

1. Select the host and domain; configure DNS and HTTPS.
2. Select a production WSGI/ASGI server and a method of serving collected static
   files. Any new software installation requires the owner's approval.
3. Provision PostgreSQL and separate the runtime role from the migration/test
   role. The public runtime role should not keep local CREATEDB privileges.
4. Set a new deployment secret, real allowed hosts, DJANGO_DEBUG=false, SMTP
   configuration and a real sender. Preserve private recovery configuration.
5. Create a real verified owner with staff MFA and replace local bootstrap access.
6. Configure the reverse proxy to overwrite forwarded headers, set only its
   actual trusted ranges, and restrict origin access to that proxy.
7. Configure edge request protection, monitoring, backup retention and a tested
   restore destination.

Domain-wide HSTS subdomains/preload are explicit opt-ins. Enable them only after
all affected names support HTTPS. A local production-profile check previously
reported those two recommendations; that check did not test external TLS or SMTP.

## Deployment checks

With actual production settings in the deployment environment:

    python manage.py check --deploy
    python manage.py migrate --check
    python manage.py collectstatic --noinput

These inspect configuration/migration state and collect static assets. They do
not provision TLS, SMTP, edge controls or a production server. Review every
reported deployment warning in the context of the chosen host.

Before accepting visitors, verify signup email delivery, recovery, MFA,
ownership checks, availability, demo success/failure/expiry/refund and both
languages over the real HTTPS domain. Inspect logs for sensitive data and
exercise request limits without blocking ordinary customers.

Keep payments visibly labelled as simulated for a public portfolio demo.
Replacing the simulator requires a separately configured provider, verified
callbacks and confirmed merchant/business facts.

## Optional Telegram worker

If enabling Telegram, configure its private token/allowlist, supervise one
run_telegram_bot process for this database, monitor pending/failed deliveries and
review the queue's customer-data retention. It requires outgoing HTTPS access
to api.telegram.org and does not expose a webhook. Verify permitted/denied
accounts and delivery recovery with the actual bot before claiming live delivery.
The local implementation and fake-transport tests do not create a BotFather bot
or configure continuous hosting. See [Telegram setup](TELEGRAM.md).

## Backup and recovery

The local backup_database command creates a private custom-format pg_dump file.
For deployment, copy backups off the application machine, apply retention and
access controls, and preserve the private key configuration needed to decrypt
MFA secrets.

Restore into a separate database first. Verify schema and booking/payment data
before switching the application to it. The local project has an earlier
successful separate-database restore rehearsal; production recovery must be
verified on the selected host.

## GitHub checks

.github/workflows/checks.yml prepares Python 3.14 and PostgreSQL 18, installs the
pinned requirements in an isolated hosted environment and runs the same source,
translation, migration and test checks as local development. Its disposable
database credentials are test-only and are unrelated to .env.

The workflow has not been executed on GitHub by this task. Its first hosted run
needs to complete after publication before a passing badge or hosted status can
be claimed.

References: [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
and [GitHub PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).
