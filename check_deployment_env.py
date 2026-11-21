"""Print non-secret deployment diagnostics; never prints passwords or full URLs."""
import os
from urllib.parse import urlsplit

role = os.getenv("APP_ENV", "").strip().lower()
url = os.getenv("DATABASE_URL", "").strip()
print(f"APP_ENV={role or 'MISSING'}")
print(f"RENDER={'yes' if os.getenv('RENDER') == 'true' else 'no'}")
print(f"SECRET_KEY={'set' if os.getenv('SECRET_KEY') else 'MISSING'}")
print(f"ADMIN_EMAIL={'set' if os.getenv('ADMIN_EMAIL') else 'MISSING'}")
print(f"ADMIN_PASSWORD={'set' if os.getenv('ADMIN_PASSWORD') else 'MISSING'}")
if url:
    try:
        parsed = urlsplit(url)
        print(f"DATABASE_SCHEME={parsed.scheme or 'unknown'}")
        print(f"DATABASE_HOST={parsed.hostname or 'unknown'}")
        print(f"DATABASE_NAME={parsed.path.lstrip('/') or 'unknown'}")
    except Exception:
        print("DATABASE_URL=present (could not parse safely)")
else:
    print("DATABASE_URL=MISSING")
print(f"UPLOAD_FOLDER={os.getenv('UPLOAD_FOLDER', 'default')}")
