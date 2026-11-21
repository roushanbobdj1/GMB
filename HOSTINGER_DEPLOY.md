# Hostinger VPS Production Deployment

Read the root `README.md` first. This Hostinger service is the **real production instance**; Render is a separate staging/test instance.

Follow `deploy/README_HOSTINGER_VPS.md`. Keep production `DATABASE_URL`, `SECRET_KEY`, uploads, and Redis private and separate from Render. Do not run migrations until a verified backup is available.
