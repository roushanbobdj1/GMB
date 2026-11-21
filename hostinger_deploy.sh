#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

python3 -m pip install --disable-pip-version-check -r requirements.txt

UPLOAD_DIR="${UPLOAD_FOLDER:-/var/lib/gmb-earn/uploads/screenshots}"
mkdir -p -- "$UPLOAD_DIR"

cat <<'MSG'
Dependencies installed and upload directory checked.
No database migration was run.
Before first production start, configure /etc/gmb-earn.env with APP_ENV=production,
the existing production DATABASE_URL, and the existing SECRET_KEY.
Back up the database and screenshots before running any migration script.
MSG
