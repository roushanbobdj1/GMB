# Hostinger deployment

This upgrade preserves existing users, wallets, points, tasks, submissions,
campaigns, support tickets, and redemption history. The migration only creates
missing tables/columns and backfills lifecycle metadata.

## Before updating

1. Back up the production database and the existing screenshot upload folder.
2. Keep the current `SECRET_KEY` unchanged. Changing it invalidates sessions
   and prevents existing encrypted payout details from being decrypted.
3. Keep the current `DATABASE_URL` unchanged so the app uses the same data.
4. Set `UPLOAD_FOLDER` to the same persistent absolute directory already used
   for screenshots.

Never upload or replace production `.env` with `.env.example`; it is only a
field-name reference.

## Required OTP email settings

For a Hostinger mailbox, use its full email address and mailbox password:

```dotenv
MAIL_SERVER=smtp.hostinger.com
MAIL_PORT=465
MAIL_USE_SSL=true
MAIL_USE_TLS=false
MAIL_USERNAME=no-reply@your-domain.com
MAIL_PASSWORD=your-mailbox-password
MAIL_DEFAULT_SENDER=no-reply@your-domain.com
TRUST_PROXY_HEADERS=true
```

If using port `587`, set `MAIL_USE_SSL=false` and `MAIL_USE_TLS=true`. Never set
both options to true.

## Deploy/update

From the project directory and with the production environment loaded:

```bash
bash hostinger_deploy.sh
```

The script installs requirements, runs the idempotent migration, and preserves
all existing rows. Run it before restarting the web service.

### Hostinger Passenger

Set the application startup file to `passenger_wsgi.py`, then restart the Python
application from hPanel. Startup also performs a fast additive schema check, so
a missed manual migration does not produce missing-column errors.

### Hostinger VPS with Gunicorn/systemd

Use one worker unless Redis is configured for shared rate limits:

```bash
python migrate_task_lifecycle.py
gunicorn --worker-class gthread --workers 1 --threads 8 --timeout 120 app:app
```

After deployment, register one test account and confirm that the registration
OTP arrives, verifies successfully, and creates exactly one user and wallet.
