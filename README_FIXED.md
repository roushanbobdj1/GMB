# GMB Backend - Fixed Build

This package contains the patched backend and templates.

## Important deployment notes

1. **Do not replace the production environment file during an update.** Keep the existing `SECRET_KEY`, `DATABASE_URL`, and `UPLOAD_FOLDER`; use `.env.example` only as a field-name reference.
2. Render and Hostinger startup run additive/idempotent schema compatibility checks that preserve user, wallet, task, submission, campaign, and redemption history.
3. Existing legacy task data is backfilled with allocation month/year, deadlines, unique-prompt usage, lifecycle counters, and activity-log entries. Take a database backup before first deployment.
4. Monthly task limits are calculated from `assigned_date`; historical tasks are no longer mutated every month.
5. Browser state-changing admin actions now use POST + CSRF protection.
6. Wallet operations and task verification are idempotent and guarded against repeated clicks/requests.
7. Rejected tasks can be resubmitted by updating the existing one-to-one submission record.
8. Rewards are tied to verified internal feedback, surveys, or mystery-shopping reports. Public Google-review URLs are optional and never determine reward eligibility.
9. Assigned/rejected tasks expire after `TASK_DEADLINE_DAYS` (default: 7) or at campaign end, whichever comes first. Released slots are automatically retried for eligible users.
10. Registration accounts are created only after a server-side, hashed email OTP is verified. OTPs expire after 15 minutes and have attempt/resend limits.

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
- `REGISTRATION_OTP_TTL_MINUTES` (default `15`)
- `AUTO_APPLY_ADDITIVE_MIGRATIONS` (default `true`)

## Run

```bash
pip install -r requirements.txt
python app.py
```

For Hostinger, follow `HOSTINGER_DEPLOY.md` and run `bash hostinger_deploy.sh` before restarting Passenger/Gunicorn.
