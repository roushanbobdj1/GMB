#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

python3 -m pip install --disable-pip-version-check -r requirements.txt

UPLOAD_DIR="${UPLOAD_FOLDER:-$APP_DIR/uploads/screenshots}"
mkdir -p -- "$UPLOAD_DIR"

python3 check_deployment_env.py || true

cat <<'MSG'
Hostinger setup step complete:
- Dependencies installed and upload folder verified.
- Existing .env and database rows were NOT modified.
- Keep your existing SECRET_KEY, DATABASE_URL, and uploads/screenshots/ intact.
- Restart Passenger or systemd (sudo systemctl restart gmb-earn) to apply changes.
MSG
