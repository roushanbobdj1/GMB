# GMB Backend - Fixed Build

This package contains the patched backend and templates.

## Important deployment notes

1. **Do not restore the old `.env` file.** Real credentials were intentionally removed from this ZIP. Copy `.env.example` to `.env` for local use or configure environment variables directly on the server.
2. The application performs safe startup migrations for the task-submission and wallet idempotency indexes and repairs wallet balances from the transaction ledger.
3. Existing legacy data is migrated automatically when the app starts. Take a database backup before first deployment.
4. Monthly task limits are calculated from `assigned_date`; historical tasks are no longer mutated every month.
5. Browser state-changing admin actions now use POST + CSRF protection.
6. Wallet operations and task verification are idempotent and guarded against repeated clicks/requests.
7. Rejected tasks can be resubmitted by updating the existing one-to-one submission record.

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

## Run

```bash
pip install -r requirements.txt
python app.py
```

For production, use the existing `Procfile`/Gunicorn deployment configuration appropriate to the Hostinger/Render setup.
