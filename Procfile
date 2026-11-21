web: gunicorn --worker-class gthread --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-10000} app:app
