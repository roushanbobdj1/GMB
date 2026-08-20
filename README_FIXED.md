# GMB Backend - Fixed Build

This package contains the patched backend and templates.

## Important deployment notes

1. **Do not restore the old `.env` file.** Real credentials were intentionally removed from this ZIP. Copy `.env.example` to `.env` for local use or configure environment variables directly on the server.
2. The Render `Procfile` runs `migrate_task_lifecycle.py` before Gunicorn. The migration is additive/idempotent and preserves task, wallet, submission, and campaign history.
3. Existing legacy task data is backfilled with allocation month/year, deadlines, unique-prompt usage, lifecycle counters, and activity-log entries. Take a database backup before first deployment.
4. Monthly task limits are calculated from `assigned_date`; historical tasks are no longer mutated every month.
5. Browser state-changing admin actions now use POST + CSRF protection.
6. Wallet operations and task verification are idempotent and guarded against repeated clicks/requests.
7. Rejected tasks can be resubmitted by updating the existing one-to-one submission record.
8. Rewards are tied to verified internal feedback, surveys, or mystery-shopping reports. Public Google-review URLs are optional and never determine reward eligibility.
9. Assigned/rejected tasks expire after `TASK_DEADLINE_DAYS` (default: 7) or at campaign end, whichever comes first. Released slots are automatically retried for eligible users.

## Environment

Required in production:

- `SECRET_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `DATABASE_URL`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`

Optional:

- `REDIS_URL` for shared rate limiting across multiple workers
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_DEFAULT_SENDER`
- `TASK_DEADLINE_DAYS` (default `7`)
- `TASK_MAINTENANCE_INTERVAL_SECONDS` (default `300`)

## Run

```bash
pip install -r requirements.txt
python app.py
```

For production, use the existing `Procfile`/Gunicorn deployment configuration appropriate to the Hostinger/Render setup.
