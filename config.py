import os
import secrets as _secrets
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

APP_ENV = os.environ.get("APP_ENV", "").strip().lower()
IS_RENDER = os.environ.get("RENDER", "").strip().lower() == "true"
IS_DEPLOYED = IS_RENDER or APP_ENV in {"staging", "production"}
IS_PRODUCTION = (
    APP_ENV == "production"
    or os.environ.get("FLASK_ENV", "").lower() == "production"
    or os.environ.get("ENVIRONMENT", "").lower() == "production"
)

# Render is intentionally the TEST/STAGING copy in this deployment design.
if IS_RENDER and APP_ENV != "staging":
    raise RuntimeError(
        "Render must use APP_ENV=staging. Do not point the Render test service "
        "at the Hostinger production database."
    )
if IS_DEPLOYED and APP_ENV not in {"staging", "production"}:
    raise RuntimeError(
        "Set APP_ENV explicitly to 'staging' on Render or 'production' on Hostinger."
    )


def _require_env(name):
    """Require secrets in every hosted environment; allow local-only defaults."""
    value = os.environ.get(name, "").strip()
    if not value:
        if IS_DEPLOYED:
            raise RuntimeError(
                f"Required environment variable '{name}' is not set. "
                "Refusing to start a hosted instance with an unsafe default."
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
        "", "replace-with-existing-database-url", "replace_with_existing_database_url",
        "replace-with-existing-production-database-url", "your-database-url",
        "postgres://user:password@host/database",
    }
    if low in placeholders or "replace_with" in low or "replace-with" in low or low.startswith("your-"):
        if IS_DEPLOYED:
            raise RuntimeError(
                "DATABASE_URL is missing or is still a placeholder. "
                "Set a dedicated database URL for this environment."
            )
        return "sqlite:///local.db"

    # Normalize common provider URL schemes to the installed SQLAlchemy drivers.
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+psycopg2://", 1)
    elif value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif value.startswith("mysql://"):
        value = value.replace("mysql://", "mysql+pymysql://", 1)
    elif value.startswith("mariadb://"):
        value = value.replace("mariadb://", "mysql+pymysql://", 1)
    return value


class Config:
    SECRET_KEY = _require_env("SECRET_KEY") or _secrets.token_hex(32)

    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Render staging is also HTTPS, so its cookies must be secure too.
    SESSION_COOKIE_SECURE = IS_DEPLOYED
    SESSION_COOKIE_NAME = "gmb_session"
    SESSION_REFRESH_EACH_REQUEST = False
    TRUST_PROXY_HEADERS = os.environ.get(
        "TRUST_PROXY_HEADERS", "false"
    ).lower() == "true"
    PREFERRED_URL_SCHEME = "https" if IS_DEPLOYED else "http"

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        "/var/lib/gmb-earn/uploads/screenshots"
        if APP_ENV == "production"
        else os.path.join(os.getcwd(), "uploads/screenshots"),
    )
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
    MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", 24_000_000))
    MAX_IMAGE_WIDTH = int(os.environ.get("MAX_IMAGE_WIDTH", 8000))
    MAX_IMAGE_HEIGHT = int(os.environ.get("MAX_IMAGE_HEIGHT", 8000))

    ADMIN_EMAIL = _require_env("ADMIN_EMAIL") or "admin@example.com"
    _admin_password_plain = _require_env("ADMIN_PASSWORD") or _secrets.token_urlsafe(12)
    if not os.environ.get("ADMIN_PASSWORD") and not IS_DEPLOYED:
        logger.warning("Generated temporary local ADMIN_PASSWORD: %s", _admin_password_plain)
    ADMIN_PASSWORD = _admin_password_plain

    # Separate prefixes prevent staging and production rate-limit keys colliding.
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "").strip() or "memory://"
    RATELIMIT_KEY_PREFIX = os.environ.get(
        "RATELIMIT_KEY_PREFIX", f"gmb-earn-{APP_ENV or 'local'}"
    )

    RUN_STARTUP_SCHEMA_MAINTENANCE = os.environ.get(
        "RUN_STARTUP_SCHEMA_MAINTENANCE", "false"
    ).lower() == "true"

    TASK_DEADLINE_DAYS = max(1, int(os.environ.get("TASK_DEADLINE_DAYS", "7")))
    TASK_MAINTENANCE_INTERVAL_SECONDS = max(
        60, int(os.environ.get("TASK_MAINTENANCE_INTERVAL_SECONDS", "300"))
    )

    # Keep false in both hosted environments. Apply reviewed migrations manually.
    AUTO_APPLY_ADDITIVE_MIGRATIONS = os.environ.get(
        "AUTO_APPLY_ADDITIVE_MIGRATIONS", "false"
    ).lower() == "true"

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
        if _socket_origins else None
    )
    SOCKETIO_ASYNC_MODE = os.environ.get("SOCKETIO_ASYNC_MODE", "threading")

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
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
    PWA_ORIENTATION = "portrait-primary"
