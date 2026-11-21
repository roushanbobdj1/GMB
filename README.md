# GMB Earn — Dual Hosting Setup (Render Staging + Hostinger Production)

## Goal

This package is designed to run **two independent copies** of GMB Earn:

| Service | Purpose | Environment | Database | Uploads |
|---|---|---|---|---|
| Render | Test new features and bug fixes | `APP_ENV=staging` | Dedicated staging/test DB | Render persistent disk |
| Hostinger VPS | Real users and real transactions | `APP_ENV=production` | Existing production DB | VPS persistent directory |

**They must never share a database, Redis instance, or upload directory.** A test admin, task, wallet change, campaign, or migration on Render must not touch Hostinger production data.

## Critical rules before deployment

1. The ZIP intentionally does not contain your `.env`, passwords, database URL, or private uploaded screenshots.
2. Do not use the same `DATABASE_URL` on Render and Hostinger.
3. Do not copy a staging database over production.
4. Keep Hostinger's current production `SECRET_KEY` unchanged. Changing it can invalidate sessions and encrypted values.
5. Do not run migrations until you have identified the database, taken a backup, and confirmed the target.
6. Keep `AUTO_APPLY_ADDITIVE_MIGRATIONS=false` on both services. Web restarts must not run database backfills.
7. Keep the existing Render service/database online until Hostinger production has been verified. If Render is your current live production today, first decide which database is the source of truth and migrate it once to Hostinger; do not simply label a copy as staging.

## 1. Render — staging/testing

1. Create a separate PostgreSQL database for staging (or use a separate Render Postgres instance). It must be empty/test data, not the production database.
2. Connect the Render web service to a staging branch such as `staging`.
3. Set the Render environment variables using `.env.render.example` as the checklist:
   - `APP_ENV=staging`
   - unique `SECRET_KEY`
   - unique staging `ADMIN_EMAIL` and `ADMIN_PASSWORD`
   - staging-only `DATABASE_URL`
   - `SOCKETIO_ALLOWED_ORIGINS=https://YOUR-SERVICE.onrender.com`
   - `AUTO_APPLY_ADDITIVE_MIGRATIONS=false`
4. If uploaded screenshots must survive deploys, attach a Render persistent disk mounted at `/var/data` and set `UPLOAD_FOLDER=/var/data/uploads/screenshots`. Without a persistent disk, Render's local filesystem may be ephemeral.
5. Use this Start Command (or leave it to the included Procfile):
   `gunicorn --worker-class gthread --workers 1 --threads 8 --timeout 120 app:app`
6. Never add the Hostinger production `DATABASE_URL`, production Redis URL, or production upload path to Render.

## 2. Hostinger VPS — production

Use Ubuntu 22.04/24.04 or another supported Linux distribution. Follow `HOSTINGER_DEPLOY.md` for the detailed command sequence. The recommended architecture is:

- Gunicorn: `127.0.0.1:8000`, one gthread worker, eight threads
- systemd: keeps the service running after reboot
- Nginx: HTTPS reverse proxy with Socket.IO WebSocket upgrade
- persistent uploads: `/var/lib/gmb-earn/uploads/screenshots`
- environment file: `/etc/gmb-earn.env` (not inside the project)

Create `/etc/gmb-earn.env` from `.env.hostinger.example`, then fill in real values privately. Do not put secrets in Git or this ZIP.

### Identify the current database first

The original uploaded `.env` contained a placeholder `DATABASE_URL`, so this source ZIP does not establish which database currently holds the real accounts and wallet records. Check the current Render service's Environment settings and identify the database type/host/database name without sharing its password in chat.

Before switching production:
- Take a database backup and verify it can be read/restored.
- Preserve the original production `SECRET_KEY`.
- Copy the existing screenshots directory to `/var/lib/gmb-earn/uploads/screenshots`.
- Confirm the source and destination record counts.
- Run reviewed migrations only against the intended production database.

### Hostinger commands

Install system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip build-essential libpq-dev nginx
sudo adduser --system --group --home /opt/gmb-earn gmbapp
sudo mkdir -p /opt/gmb-earn/current /var/lib/gmb-earn/uploads/screenshots
sudo chown -R gmbapp:www-data /opt/gmb-earn /var/lib/gmb-earn
```

Extract this ZIP into `/opt/gmb-earn/current`, then:

```bash
sudo -u gmbapp python3 -m venv /opt/gmb-earn/venv
sudo -u gmbapp /opt/gmb-earn/venv/bin/pip install --upgrade pip wheel
sudo -u gmbapp /opt/gmb-earn/venv/bin/pip install -r /opt/gmb-earn/current/requirements.txt
sudo cp /opt/gmb-earn/current/.env.hostinger.example /etc/gmb-earn.env
sudo nano /etc/gmb-earn.env
sudo chown root:gmbapp /etc/gmb-earn.env
sudo chmod 640 /etc/gmb-earn.env
sudo chown -R gmbapp:www-data /var/lib/gmb-earn
```

Replace placeholders in `/etc/gmb-earn.env`. Set the correct database URL (existing live DB only), current production secret, admin credentials, domain, and SMTP settings.

Install service and Nginx:

```bash
sudo cp /opt/gmb-earn/current/deploy/gmb-earn.service /etc/systemd/system/gmb-earn.service
sudo systemctl daemon-reload
sudo systemctl enable --now gmb-earn
sudo cp /opt/gmb-earn/current/deploy/nginx-gmb-earn.conf /etc/nginx/sites-available/gmb-earn
sudo nano /etc/nginx/sites-available/gmb-earn
sudo ln -s /etc/nginx/sites-available/gmb-earn /etc/nginx/sites-enabled/gmb-earn
sudo nginx -t
sudo systemctl reload nginx
```

Replace `YOUR_DOMAIN` in Nginx config. Point DNS A records to the VPS IP, then install TLS using Certbot and enable HTTPS. Keep `TRUST_PROXY_HEADERS=true` only behind this trusted Nginx proxy.

## 3. Database migrations

The project has two migration scripts:
- `migrate_task_lifecycle.py` includes historical backfill logic; do not run it casually or on every app boot.
- `migrate_points_backfill.py` is also a data migration; review and back up first.

After backup and after verifying `DATABASE_URL`, run only the migration(s) required for that database/schema. Example:

```bash
cd /opt/gmb-earn/current
sudo -u gmbapp bash -lc 'set -a; source /etc/gmb-earn.env; set +a; /opt/gmb-earn/venv/bin/python migrate_task_lifecycle.py'
```

This is an operator action, not part of the Gunicorn Start Command. If this is an existing production DB, compare record counts and verify wallet balances after migration.

## 4. Development workflow: staging → production

1. Make changes on the `staging` Git branch.
2. Render deploys `staging`; test login, registration/OTP, task flows, screenshots, wallet, redemptions, admin actions, support, PWA, and Socket.IO.
3. Fix bugs on staging and redeploy Render.
4. When accepted, merge the tested commit into `main` (or your production branch).
5. Deploy that reviewed commit to Hostinger. Back up production DB and uploads before any schema/data migration.
6. Never sync Render's test DB/uploads into Hostinger.

## 5. Verify both services

On Hostinger:

```bash
sudo systemctl status gmb-earn --no-pager
sudo journalctl -u gmb-earn -n 100 --no-pager
sudo nginx -t
```

Test real production flows using a controlled test account. Do not create test transactions in real user accounts.

## 6. Troubleshooting

- **Service refuses to start:** check `APP_ENV`, `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and `DATABASE_URL`.
- **Render refuses to start:** ensure `APP_ENV=staging` (not `production`).
- **Wallet/users appear missing:** stop testing and verify which database URL the service is using. Do not create a new empty DB or overwrite the old one.
- **Socket.IO disconnects:** confirm Gunicorn is using `gthread`, Nginx has `Upgrade`/`Connection` headers, and `SOCKETIO_ALLOWED_ORIGINS` matches the exact HTTPS domain.
- **Screenshots disappear after deploy:** configure persistent storage and set `UPLOAD_FOLDER` to its mounted path.

See `HOSTINGER_DEPLOY.md` and `deploy/README_HOSTINGER_VPS.md` for Hostinger-specific steps.
