# Render Staging / Testing

This Render service is the non-production test copy only.

Required environment:
- `APP_ENV=staging`
- unique staging `SECRET_KEY`, admin credentials, and `DATABASE_URL`
- staging-only Redis if used
- persistent disk mounted at `/var/data` for uploads, with `UPLOAD_FOLDER=/var/data/uploads/screenshots`
- `AUTO_APPLY_ADDITIVE_MIGRATIONS=false`

Start command:
`gunicorn --worker-class gthread --workers 1 --threads 8 --timeout 120 app:app`

Render's `RENDER=true` environment flag does not make the app production: the code requires `APP_ENV=staging` for Render. Never connect this service to the Hostinger production database.
