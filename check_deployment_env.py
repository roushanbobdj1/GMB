"""Print non-secret deployment diagnostics for Hostinger and Render."""
import os
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)
except ImportError:
    pass

role = os.getenv("APP_ENV", "").strip().lower()
url = os.getenv("DATABASE_URL", "").strip()
upload_dir = os.getenv("UPLOAD_FOLDER", "").strip() or os.path.join(BASE_DIR, "uploads", "screenshots")

print(f"APP_ENV={role or 'auto'}")
print(f"RENDER={'yes' if os.getenv('RENDER', '').lower() == 'true' else 'no'}")
print(f"SECRET_KEY={'set' if os.getenv('SECRET_KEY') else 'MISSING'}")
print(f"ADMIN_EMAIL={'set' if os.getenv('ADMIN_EMAIL') else 'MISSING'}")
print(f"ADMIN_PASSWORD={'set' if os.getenv('ADMIN_PASSWORD') else 'MISSING'}")
print(f"AUTO_APPLY_ADDITIVE_MIGRATIONS={os.getenv('AUTO_APPLY_ADDITIVE_MIGRATIONS', 'true (default)')}")
print(f"TRUST_PROXY_HEADERS={os.getenv('TRUST_PROXY_HEADERS', 'auto (true when deployed)')}")
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
print(f"UPLOAD_FOLDER={upload_dir} (exists={os.path.isdir(upload_dir)}, writable={os.access(upload_dir, os.W_OK)})")
