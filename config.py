import os
import secrets as _secrets
import logging
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env from project directory (never overriding platform env vars on Render / systemd)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)

IS_RENDER = os.environ.get("RENDER", "").strip().lower() == "true"
_RAW_APP_ENV = os.environ.get("APP_ENV", "").strip().lower()
_RAW_DB_URL = os.environ.get("DATABASE_URL", "").strip()
_HAS_EXTERNAL_DB = bool(_RAW_DB_URL) and not _RAW_DB_URL.lower().startswith("sqlite")

if _RAW_APP_ENV in {"production", "staging", "development", "local"}:
    APP_ENV = _RAW_APP_ENV
elif (
    IS_RENDER
    or _HAS_EXTERNAL_DB
    or os.environ.get("FLASK_ENV", "").strip().lower() == "production"
    or os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
):
    APP_ENV = "production"
else:
    APP_ENV = "development"

IS_DEPLOYED = IS_RENDER or APP_ENV in {"staging", "production"}
IS_PRODUCTION = APP_ENV == "production"


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require_env(name):
    """Require secrets in hosted environments; allow temporary defaults locally."""
    value = os.environ.get(name, "").strip()
    if not value:
        if IS_DEPLOYED:
            raise RuntimeError(
                f"Required environment variable '{name}' is not set. "
                "Set it in your Hostinger (.env / /etc/gmb-earn.env) or Render Environment settings."
            )
        logger.warning(
            "%s is not set; using a temporary local-development value only.", name
        )
        return None
    return value


def _database_url():
    value = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    low = value.lower()
    placeholders = {
        "",
        "replace-with-existing-database-url",
        "replace_with_existing_database_url",
        "replace-with-existing-production-database-url",
        "your-database-url",
        "postgres://user:password@host/database",
        "mysql://user:password@host/database",
    }
    if (
        low in placeholders
        or "replace_with" in low
        or "replace-with" in low
        or low.startswith("your-")
    ):
        if IS_DEPLOYED:
            raise RuntimeError(
                "DATABASE_URL is missing or still a placeholder. "
                "Set your Hostinger or Render DATABASE_URL before starting."
            )
        return f"sqlite:///{os.path.join(BASE_DIR, 'local.db')}"

    # Normalize common provider URL schemes (Render PostgreSQL + Hostinger MySQL/MariaDB/PostgreSQL).
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+psycopg2://", 1)
    elif value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif value.startswith("mysql://"):
        value = value.replace("mysql://", "mysql+pymysql://", 1)
    elif value.startswith("mariadb://"):
        value = value.replace("mariadb://", "mysql+pymysql://", 1)
    return value


def _resolve_upload_folder():
    configured = os.environ.get("UPLOAD_FOLDER", "").strip()
    if configured:
        if os.path.isabs(configured):
            return configured
        return os.path.join(BASE_DIR, configured)
    return os.path.join(BASE_DIR, "uploads", "screenshots")


def _allowed_extensions():
    safe_pool = {"png", "jpg", "jpeg"}
    raw = os.environ.get("ALLOWED_EXTENSIONS", "").strip()
    if not raw:
        return safe_pool
    parsed = {ext.strip().lower().lstrip(".") for ext in raw.split(",") if ext.strip()}
    filtered = parsed & safe_pool
    return filtered or safe_pool


class Config:
    BASE_DIR = BASE_DIR
    APP_ENV = APP_ENV
    IS_RENDER = IS_RENDER
    IS_DEPLOYED = IS_DEPLOYED
    IS_PRODUCTION = IS_PRODUCTION

    SECRET_KEY = _require_env("SECRET_KEY") or _secrets.token_hex(32)

    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", default=IS_DEPLOYED)
    SESSION_COOKIE_NAME = "gmb_session"
    SESSION_REFRESH_EACH_REQUEST = False

    # Both Render and Hostinger sit behind a reverse proxy (HTTPS termination).
    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", default=IS_DEPLOYED)
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = _resolve_upload_folder()
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = _allowed_extensions()
    MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", 24_000_000))
    MAX_IMAGE_WIDTH = int(os.environ.get("MAX_IMAGE_WIDTH", 8000))
    MAX_IMAGE_HEIGHT = int(os.environ.get("MAX_IMAGE_HEIGHT", 8000))

    ADMIN_EMAIL = _require_env("ADMIN_EMAIL") or "admin@example.com"
    _admin_password_plain = _require_env("ADMIN_PASSWORD") or _secrets.token_urlsafe(12)
    if not os.environ.get("ADMIN_PASSWORD") and not IS_DEPLOYED:
        logger.warning("Generated temporary local ADMIN_PASSWORD: %s", _admin_password_plain)
    ADMIN_PASSWORD = _admin_password_plain

    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "").strip() or "memory://"
    RATELIMIT_KEY_PREFIX = os.environ.get(
        "RATELIMIT_KEY_PREFIX", f"gmb-earn-{APP_ENV or 'local'}"
    )

    RUN_STARTUP_SCHEMA_MAINTENANCE = _env_bool("RUN_STARTUP_SCHEMA_MAINTENANCE", default=False)

    TASK_DEADLINE_DAYS = max(1, int(os.environ.get("TASK_DEADLINE_DAYS", "7")))
    TASK_MAINTENANCE_INTERVAL_SECONDS = max(
        60, int(os.environ.get("TASK_MAINTENANCE_INTERVAL_SECONDS", "300"))
    )

    # Additive-only & non-destructive: safely adds missing tables/columns so existing
    # Hostinger users never hit schema errors after an update.
    AUTO_APPLY_ADDITIVE_MIGRATIONS = _env_bool("AUTO_APPLY_ADDITIVE_MIGRATIONS", default=True)

    REGISTRATION_OTP_TTL_MINUTES = max(
        5, int(os.environ.get("REGISTRATION_OTP_TTL_MINUTES", "15"))
    )
    REGISTRATION_OTP_MAX_ATTEMPTS = max(
        3, int(os.environ.get("REGISTRATION_OTP_MAX_ATTEMPTS", "5"))
    )
    REGISTRATION_OTP_RESEND_COOLDOWN_SECONDS = max(
        30, int(os.environ.get("REGISTRATION_OTP_RESEND_COOLDOWN_SECONDS", "60"))
    )
    REGISTRATION_OTP_MAX_RESENDS = max(
        1, int(os.environ.get("REGISTRATION_OTP_MAX_RESENDS", "5"))
    )
    REGISTRATION_OTP_RETENTION_DAYS = max(
        1, int(os.environ.get("REGISTRATION_OTP_RETENTION_DAYS", "7"))
    )

    _socket_origins = os.environ.get("SOCKETIO_ALLOWED_ORIGINS", "").strip()
    SOCKETIO_ALLOWED_ORIGINS = (
        [origin.strip() for origin in _socket_origins.split(",") if origin.strip()]
        if _socket_origins
        else None
    )
    SOCKETIO_ASYNC_MODE = os.environ.get("SOCKETIO_ASYNC_MODE", "threading").strip() or "threading"

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", default=True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", default=False)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
    MAIL_TIMEOUT = max(5, int(os.environ.get("MAIL_TIMEOUT", "20")))

    PWA_NAME = "GMB Feedback & Task Platform"
    PWA_SHORT_NAME = "GMB Earn"
    PWA_START_URL = "/"
    PWA_DISPLAY = "standalone"
    PWA_THEME_COLOR = "#0d6efd"
    PWA_BACKGROUND_COLOR = "#ffffff"
    PWA_ORIENTATION = "any"
