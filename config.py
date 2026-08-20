import os
import secrets as _secrets
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

# Treat as production if explicitly on Render or FLASK_ENV=production.
IS_PRODUCTION = (
    os.environ.get('RENDER') == 'true'
    or os.environ.get('FLASK_ENV', '').lower() == 'production'
    or os.environ.get('APP_ENV', '').lower() == 'production'
    or os.environ.get('ENVIRONMENT', '').lower() == 'production'
)


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

    # User sessions are long-lived for the installed PWA, but admin sessions
    # are deliberately non-permanent in app.py.
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # ---------- Session / cookie security ----------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION  # only send cookie over HTTPS in production
    SESSION_COOKIE_NAME = 'gmb_session'
    SESSION_REFRESH_EACH_REQUEST = False
    TRUST_PROXY_HEADERS = os.environ.get(
        'TRUST_PROXY_HEADERS', 'false'
    ).lower() == 'true'
    PREFERRED_URL_SCHEME = 'https' if IS_PRODUCTION else 'http'

    # ---------- Database (Render fix) ----------
    _db_url = os.environ.get('DATABASE_URL', '')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or 'sqlite:///local.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---------- Upload Configuration ----------
    # Point this at a mounted persistent disk in production. Local container
    # filesystems (for example Render's default disk) are ephemeral.
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER', os.path.join(os.getcwd(), 'uploads/screenshots')
    )
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
    MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS', 24_000_000))
    MAX_IMAGE_WIDTH = int(os.environ.get('MAX_IMAGE_WIDTH', 8000))
    MAX_IMAGE_HEIGHT = int(os.environ.get('MAX_IMAGE_HEIGHT', 8000))

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
    RATELIMIT_KEY_PREFIX = os.environ.get('RATELIMIT_KEY_PREFIX', 'gmb-earn')

    # Database creation/schema repair is an explicit maintenance action. It is
    # never run merely because a web worker started.
    RUN_STARTUP_SCHEMA_MAINTENANCE = os.environ.get(
        'RUN_STARTUP_SCHEMA_MAINTENANCE', 'false'
    ).lower() == 'true'

    # Task lifecycle maintenance is opportunistic and idempotent. A request
    # may trigger it after this interval; no destructive monthly reset runs.
    TASK_DEADLINE_DAYS = max(1, int(os.environ.get('TASK_DEADLINE_DAYS', '7')))
    TASK_MAINTENANCE_INTERVAL_SECONDS = max(
        60, int(os.environ.get('TASK_MAINTENANCE_INTERVAL_SECONDS', '300'))
    )

    # Additive migrations are safe to run during Hostinger/Passenger startup.
    # They never drop tables/columns or rewrite wallet balances.
    AUTO_APPLY_ADDITIVE_MIGRATIONS = os.environ.get(
        'AUTO_APPLY_ADDITIVE_MIGRATIONS', 'true'
    ).lower() == 'true'

    REGISTRATION_OTP_TTL_MINUTES = max(
        5, int(os.environ.get('REGISTRATION_OTP_TTL_MINUTES', '15'))
    )
    REGISTRATION_OTP_MAX_ATTEMPTS = max(
        3, int(os.environ.get('REGISTRATION_OTP_MAX_ATTEMPTS', '5'))
    )
    REGISTRATION_OTP_RESEND_COOLDOWN_SECONDS = max(
        30, int(os.environ.get('REGISTRATION_OTP_RESEND_COOLDOWN_SECONDS', '60'))
    )
    REGISTRATION_OTP_MAX_RESENDS = max(
        1, int(os.environ.get('REGISTRATION_OTP_MAX_RESENDS', '5'))
    )
    REGISTRATION_OTP_RETENTION_DAYS = max(
        1, int(os.environ.get('REGISTRATION_OTP_RETENTION_DAYS', '7'))
    )

    # Comma-separated origins can be supplied in production. Same-origin is
    # the safe default and does not expose cookie-authenticated Socket.IO to
    # arbitrary websites.
    _socket_origins = os.environ.get('SOCKETIO_ALLOWED_ORIGINS', '').strip()
    SOCKETIO_ALLOWED_ORIGINS = (
        [origin.strip() for origin in _socket_origins.split(',') if origin.strip()]
        if _socket_origins else None
    )
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')

    # ---------- Mail config ----------
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME)
    MAIL_TIMEOUT = max(5, int(os.environ.get('MAIL_TIMEOUT', '20')))

    # ============ PWA CONFIG ============
    PWA_NAME = "GMB Feedback & Task Platform"
    PWA_SHORT_NAME = "GMB Earn"
    PWA_START_URL = "/"
    PWA_DISPLAY = "standalone"
    PWA_THEME_COLOR = "#0d6efd"
    PWA_BACKGROUND_COLOR = "#ffffff"
    PWA_ORIENTATION = "portrait-primary"
