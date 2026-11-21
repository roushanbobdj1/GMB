# GMB Earn — Dual Deployment Guide (Hostinger + Render)

This package is configured to run cleanly on **both Hostinger (Shared/Cloud Python Passenger or VPS)** and **Render**, while keeping existing Hostinger user data completely safe.

## Important Safety Rules for Existing Hostinger Users

1. **Keep your existing `.env` (or `/etc/gmb-earn.env`)** — This ZIP intentionally excludes `.env` so extracting it will NOT overwrite your live `SECRET_KEY`, `DATABASE_URL`, or mail credentials.
2. **Never change `SECRET_KEY` on Hostinger** — Existing user sessions and encrypted payment details depend on your current `SECRET_KEY`.
3. **Keep your existing `uploads/screenshots/` folder** — This ZIP only ships `uploads/screenshots/.gitkeep` (no test images) so your live screenshots remain untouched.
4. **Automatic non-destructive schema safety** — On startup (`AUTO_APPLY_ADDITIVE_MIGRATIONS=true`), the app only creates missing tables/columns (works for MySQL, MariaDB, PostgreSQL, and SQLite) and never drops tables or deletes user/wallet/task rows.
5. **Take a DB backup first** — Always export a quick SQL backup on Hostinger before deploying any update.

---

## Option A: Deploy / Update on Hostinger

### 1. Hostinger Shared / Cloud Python (Passenger WSGI)
1. Upload and extract this ZIP into your app directory (do **not** delete your existing `.env` or `uploads/screenshots/`).
2. Ensure your `.env` has your existing `SECRET_KEY`, `DATABASE_URL` (MySQL or PostgreSQL), `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and SMTP values (see `.env.example`).
3. Install dependencies:
   ```bash
   bash hostinger_deploy.sh
   ```
4. Set Passenger startup file to `passenger_wsgi.py` (entrypoint: `application`) and restart the Python app from hPanel.

### 2. Hostinger VPS (Gunicorn + systemd + Nginx)
1. Extract into `/opt/gmb-earn/current` (keeping `/etc/gmb-earn.env` and `/var/lib/gmb-earn/uploads/screenshots` unchanged).
2. Install dependencies inside your venv:
   ```bash
   /opt/gmb-earn/venv/bin/pip install -r /opt/gmb-earn/current/requirements.txt
   ```
3. Use `deploy/gmb-earn.service` and `deploy/nginx-gmb-earn.conf` (replace `YOUR_DOMAIN`), then restart:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart gmb-earn
   sudo systemctl reload nginx
   ```

### Optional One-Time Data Backfills (Only if upgrading from a very old schema)
After taking a DB backup, you may optionally run:
```bash
python3 migrate_points_backfill.py
python3 migrate_task_lifecycle.py
```
Both scripts are idempotent (safe to re-run) and never delete user or wallet rows.

---

## Option B: Deploy on Render

1. Push this repo to GitHub (or connect via Blueprint using `render.yaml`).
2. Render automatically uses:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn --worker-class gthread --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app`
   - **Health Check Path:** `/health`
3. Set these Environment Variables in Render Dashboard:
   - `APP_ENV` = `production` (or `staging`)
   - `SECRET_KEY` = strong random secret
   - `DATABASE_URL` = your Render PostgreSQL URL (keep separate from Hostinger DB if testing!)
   - `ADMIN_EMAIL` & `ADMIN_PASSWORD`
   - `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_USERNAME`, `MAIL_PASSWORD`
   - *(Optional)* Attach a Render Persistent Disk at `/var/data` and set `UPLOAD_FOLDER=/var/data/uploads/screenshots` so uploaded screenshots persist across redeploys.
