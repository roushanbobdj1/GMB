#!/usr/bin/env bash
set -euo pipefail

TASK_APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$TASK_APP_DIR"

"$TASK_PYTHON_BIN" -m pip install --disable-pip-version-check -r requirements.txt
"$TASK_PYTHON_BIN" migrate_task_lifecycle.py

TASK_UPLOAD_DIR="${UPLOAD_FOLDER:-$TASK_APP_DIR/uploads/screenshots}"
mkdir -p -- "$TASK_UPLOAD_DIR"

echo "Dependencies and additive database migration completed."
echo "Restart Passenger from hPanel, or restart your GMB systemd/Gunicorn service."
