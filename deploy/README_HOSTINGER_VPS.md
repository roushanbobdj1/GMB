# Hostinger VPS — Production Runbook

The recommended setup is Ubuntu + Gunicorn gthread + systemd + Nginx. See the root `README.md` for the complete dual-environment workflow and safety rules.

## Important

- `APP_ENV=production`
- `DATABASE_URL` must point to the existing live database that holds real user/wallet/task records.
- Keep the current production `SECRET_KEY`.
- Store secrets in `/etc/gmb-earn.env`, never in the project directory.
- Keep uploads at `/var/lib/gmb-earn/uploads/screenshots`.
- Do not run either migration script before backup and review.
- Do not share the database, Redis, or upload storage with Render staging.

## Service files

- `gmb-earn.service`: Gunicorn on `127.0.0.1:8000`, one gthread worker / eight threads.
- `nginx-gmb-earn.conf`: reverse proxy with WebSocket upgrade support.

See root `README.md` for installation, environment, migration, DNS, SSL, and verification commands.
