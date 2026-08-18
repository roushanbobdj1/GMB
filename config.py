import os
import secrets as _secrets
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

# Treat as production if explicitly on Render or FLASK_ENV=production.
IS_PRODUCTION = os.environ.get('RENDER') == 'true' or os.environ.get('FLASK_ENV') == 'production'


def _require_env(name):
    """Fail fast in production if a required secret isn't set."""
    value = os.environ.get(name, '').strip()
    if not value:
        if IS_PRODUCTION:
            raise RuntimeError(
                f"❌ Required environment variable '{name}' is not set. "
                f"Refusing to start in production without it."
            )
        logger.warning(
            f"⚠️  {name} not set – using an auto-generated value for local development only. "
            f"Set {name} in your environment before deploying."
        )
        return None
    return value


class Config:
    # ---------- Secret key ----------
    # No hardcoded fallback. In production this MUST come from the environment.
    # In development, fall back to a random key (regenerated each run, sessions
    # won't persist across restarts, which is fine for local dev).
    SECRET_KEY = _require_env('SECRET_KEY') or _secrets.token_hex(32)

    PERMANENT_SESSION_LIFETIME = timedelta(days=365)

    # ---------- Session / cookie security ----------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION  # only send cookie over HTTPS in production
    SESSION_COOKIE_NAME = 'gmb_session'
    SESSION_REFRESH_EACH_REQUEST = False

    # ---------- Database (Render fix) ----------
    _db_url = os.environ.get('DATABASE_URL', '')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or 'sqlite:///local.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---------- Upload Configuration ----------
    UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads/screenshots')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

    # ---------- Admin credentials ----------
    # No weak hardcoded defaults. Must be set via environment.
    ADMIN_EMAIL = _require_env('ADMIN_EMAIL') or 'admin@example.com'
    _admin_password_plain = _require_env('ADMIN_PASSWORD') or _secrets.token_urlsafe(12)
    if not os.environ.get('ADMIN_PASSWORD') and not IS_PRODUCTION:
        # Surface the generated dev password once so the developer can log in locally.
        logger.warning(f"⚠️  Generated temporary ADMIN_PASSWORD for local dev: {_admin_password_plain}")
    ADMIN_PASSWORD = _admin_password_plain

    # ---------- Rate limiting ----------
    RATELIMIT_STORAGE_URI = os.environ.get('REDIS_URL', 'memory://')

    # ---------- Mail config ----------
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME)

    # ============ PWA CONFIG ============
    PWA_NAME = "GMB Review Earning Platform"
    PWA_SHORT_NAME = "GMB Earn"
    PWA_START_URL = "/"
    PWA_DISPLAY = "standalone"
    PWA_THEME_COLOR = "#0d6efd"
    PWA_BACKGROUND_COLOR = "#ffffff"
    PWA_ORIENTATION = "portrait-primary"
