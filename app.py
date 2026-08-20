# Combined, Cleaned, and Production-Ready app.py for Hostinger VPS
import os
import re
import uuid
import random
import logging
import hashlib
import secrets
import base64
from io import BytesIO
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect, session, url_for, flash,
    jsonify, send_file, send_from_directory, make_response
)
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError

# Optional: enables local .env file support.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy import event, func, and_, or_, inspect, text
from sqlalchemy.orm import Session as SASession

# App config + models
from config import Config
from models import *  # expecting db, User, Wallet, Task, TaskSubmission, Notification, Campaign, CampaignReviewText, CampaignAllocationProgress, UserCampaignTaskAssignment, RedeemRequest, WalletTransaction, PasswordResetToken, SupportTicket, SupportReply, LeaderboardSettings, LeaderboardExclusion

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask App
app = Flask(__name__)
app.config.from_object(Config)

# Database pooling settings to prevent drop connections
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,  
    "pool_recycle": 300,    
}

# Real Email Settings (Active)
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', app.config.get('MAIL_SERVER', 'smtp.gmail.com'))
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', app.config.get('MAIL_PORT', 587)))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', str(app.config.get('MAIL_USE_TLS', True))).lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', str(app.config.get('MAIL_USE_SSL', False))).lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', app.config.get('MAIL_USERNAME', ''))
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', os.environ.get('MAIL_APP_PASSWORD', app.config.get('MAIL_PASSWORD', '')))
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', app.config.get('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME']))

mail = Mail(app)

db.init_app(app)

# Ensure upload folder exists
os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)

# SocketIO (real-time)
socketio = SocketIO(
    app,
    cors_allowed_origins=app.config.get('SOCKETIO_ALLOWED_ORIGINS'),
    async_mode="gevent",
    logger=False,
    engineio_logger=False
)

# CSRF protection
csrf = CSRFProtect(app)

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri=app.config.get('RATELIMIT_STORAGE_URI', 'memory://'),
    key_prefix=app.config.get('RATELIMIT_KEY_PREFIX', 'gmb-earn'),
)

if app.config.get('RATELIMIT_STORAGE_URI') == 'memory://' and os.environ.get('RENDER') == 'true':
    logger.warning(
        'REDIS_URL is not configured. Rate limits are process-local; keep one web worker '
        'or configure Redis before scaling horizontally.'
    )

# Admin password hashing fallback
admin_password_str = app.config['ADMIN_PASSWORD']
_ADMIN_PASSWORD_HASH = bcrypt.hashpw(admin_password_str.encode('utf-8'), bcrypt.gensalt())
_ADMIN_CREDENTIAL_FINGERPRINT = hashlib.sha256(
    f"{app.config['ADMIN_EMAIL'].strip().lower()}\0{admin_password_str}".encode('utf-8')
).hexdigest()
_SENSITIVE_DATA_CIPHER = Fernet(base64.urlsafe_b64encode(
    hashlib.sha256(app.config['SECRET_KEY'].encode('utf-8')).digest()
))


def check_admin_password(candidate: str) -> bool:
    try:
        return bcrypt.checkpw(candidate.encode('utf-8'), _ADMIN_PASSWORD_HASH)
    except Exception:
        return False


# ----------------- Helpers & Utilities -----------------

@app.context_processor
def inject_nav_user_info():
    data = {'nav_user': None, 'nav_available_points': 0, 'nav_unread_count': 0}
    try:
        user_id = session.get('user_id')
        if user_id:
            user = db.session.get(User, user_id)
            if user:
                data['nav_user'] = user
                wallet = Wallet.query.filter_by(user_id=user_id).first()
                data['nav_available_points'] = wallet.available_points if wallet else 0
                data['nav_unread_count'] = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    except Exception as e:
        logger.warning(f"nav context processor error: {e}")
    return data


def utc_now():
    """Return a naive UTC datetime, matching the existing DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value):
    """Serialize existing naive/aware values as valid UTC ISO-8601."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


def password_fingerprint(user):
    return hashlib.sha256((user.password or '').encode('utf-8')).hexdigest()


def encrypt_sensitive(value):
    if not value:
        return value
    return 'enc:' + _SENSITIVE_DATA_CIPHER.encrypt(value.encode('utf-8')).decode('ascii')


@app.template_filter('decrypt_sensitive')
def decrypt_sensitive(value):
    """Decrypt new values while remaining compatible with legacy plaintext rows."""
    if not value or not value.startswith('enc:'):
        return value
    try:
        return _SENSITIVE_DATA_CIPHER.decrypt(value[4:].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeError):
        logger.error('Unable to decrypt a sensitive database value.')
        return '[unavailable]'


def notify_user(user_id, message, notification_type='general', related_id=None, extra=None):
    """Queue a notification in the caller's transaction and emit its event.

    This helper intentionally never commits or rolls back. Transaction ownership
    remains with the route/service that changed the related business state.
    """
    notif = Notification(
        user_id=user_id,
        message=message,
        notification_type=notification_type,
        related_id=related_id,
        is_read=False
    )
    db.session.add(notif)

    payload = {
        'message': message,
        'type': notification_type,
        'related_id': related_id,
        'created_at': utc_now().strftime('%d-%m-%Y %H:%M')
    }
    if extra:
        payload.update(extra)
    db.session.info.setdefault('pending_socket_notifications', []).append((user_id, payload))
    return notif


@event.listens_for(SASession, 'after_commit')
def emit_committed_notifications(sqlalchemy_session):
    """Publish real-time notifications only after their DB transaction commits."""
    pending = sqlalchemy_session.info.pop('pending_socket_notifications', [])
    for user_id, payload in pending:
        socketio.emit('new_notification', payload, room=f'user_{user_id}')


@event.listens_for(SASession, 'after_rollback')
def discard_rolled_back_notifications(sqlalchemy_session):
    sqlalchemy_session.info.pop('pending_socket_notifications', None)


def notify_admins(event, data):
    socketio.emit(event, data, room='admin_room')


def broadcast_leaderboard_update():
    socketio.emit('leaderboard_updated', {})


@socketio.on('connect')
def ws_connect():
    if session.get('user_id'):
        join_room(f"user_{session['user_id']}")
    if session.get('admin_id'):
        join_room('admin_room')


@socketio.on('join_ticket')
def ws_join_ticket(data):
    ticket_id = data.get('ticket_id') if isinstance(data, dict) else None
    if not ticket_id:
        return
    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket:
        return
    is_owner = session.get('user_id') and ticket.user_id == session.get('user_id')
    is_admin = bool(session.get('admin_id'))
    if is_owner or is_admin:
        join_room(f"ticket_{ticket_id}")


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg'})


def is_valid_image_content(file_storage):
    try:
        file_storage.stream.seek(0)
        Image.MAX_IMAGE_PIXELS = app.config.get('MAX_IMAGE_PIXELS', 24_000_000)
        img = Image.open(file_storage.stream)
        width, height = img.size
        if width > app.config.get('MAX_IMAGE_WIDTH', 8000) or height > app.config.get('MAX_IMAGE_HEIGHT', 8000):
            return False
        img.verify()
        file_storage.stream.seek(0)
        img2 = Image.open(file_storage.stream)
        fmt = (img2.format or '').lower()
        file_storage.stream.seek(0)
        allowed_formats = {'png', 'jpeg', 'jpg'}
        return fmt in allowed_formats
    except (UnidentifiedImageError, OSError, ValueError, Exception):
        return False


def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format"
    return True, "Valid email"


def is_valid_password(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain lowercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Password must contain number"
    if not re.search(r'[!@#$%^&*]', password):
        return False, "Password must contain special character (!@#$%^&*)"
    return True, "Password is strong"


def is_valid_name(name):
    if len(name.strip()) < 3:
        return False, "Name must be at least 3 characters"
    if not re.match(r'^[a-zA-Z\s]+$', name):
        return False, "Name can only contain letters and spaces"
    return True, "Name is valid"


def is_valid_phone(phone):
    if not phone or len(phone) == 0:
        return False, "Phone number is required"
    if not re.match(r'^[0-9]{10}$', phone):
        return False, "Phone must be exactly 10 digits"
    return True, "Phone is valid"


def is_safe_http_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in {'http', 'https'} and bool(parsed.netloc)
    except (TypeError, ValueError):
        return False


# ----------------- Authentication wrappers & request hooks -----------------

@app.before_request
def check_if_blocked():
    user_id = session.get('user_id')

    if not user_id:
        return None

    try:
        user = db.session.get(User, user_id)

        # Only logout when we have confirmed that the
        # user genuinely does not exist in the database.
        if user is None:
            logger.warning(
                "Authenticated session points to missing user_id=%s",
                user_id
            )

            session.pop('user_id', None)
            session.pop('user_email', None)
            session.pop('user_name', None)

            flash(
                '❌ Your account no longer exists. Please login again.',
                'error'
            )

            return redirect(url_for('user_login'))

        current_fingerprint = password_fingerprint(user)
        session_fingerprint = session.get('password_fingerprint')
        if session_fingerprint and not secrets.compare_digest(session_fingerprint, current_fingerprint):
            session.clear()
            flash('🔐 Your password changed. Please login again.', 'info')
            return redirect(url_for('user_login'))
        if not session_fingerprint:
            # Upgrade existing signed sessions without forcing a logout.
            session['password_fingerprint'] = current_fingerprint

        if getattr(user, 'is_blocked', False):
            allowed_routes = [
                'support_page',
                'create_support_ticket',
                'view_ticket',
                'reply_ticket',
                'logout',
                'static',
                'offline'
            ]

            if (
                request.endpoint
                and request.endpoint not in allowed_routes
            ):
                flash(
                    '❌ Your account has been blocked by Admin. '
                    'Please contact support.',
                    'error'
                )

                return redirect(url_for('support_page'))

    except SQLAlchemyError as e:
        # IMPORTANT:
        # Do NOT clear the user's session when the database
        # temporarily has a connection/query problem.
        db.session.rollback()

        logger.error(
            "Database error while validating session for user_id=%s: %s",
            user_id,
            e
        )

        # Let the request continue instead of logging the user out.
        return None

    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('❌ Please login first!', 'error')
            return redirect(url_for('user_login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        fingerprint = session.get('admin_credential_fingerprint', '')
        if (
            'admin_id' not in session
            or not fingerprint
            or not secrets.compare_digest(fingerprint, _ADMIN_CREDENTIAL_FINGERPRINT)
        ):
            session.clear()
            flash('❌ Admin login required!', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# ----------------- DB Auto-migration helper -----------------

def _create_missing_indexes():
    """Apply only additive, non-destructive compatibility changes.

    This function is disabled by default and must only be run as an explicit
    maintenance action. It never deletes rows or rewrites financial balances.
    """
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if 'task_submissions' in tables:
        cols = {c['name'] for c in inspector.get_columns('task_submissions')}
        if 'screenshot_hash' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE task_submissions ADD COLUMN screenshot_hash VARCHAR(64)'))

    if 'tasks' in tables:
        cols = {c['name'] for c in inspector.get_columns('tasks')}
        if 'points_per_review' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE tasks ADD COLUMN points_per_review INTEGER DEFAULT 0'))

    if 'campaigns' in tables:
        cols = {c['name'] for c in inspector.get_columns('campaigns')}
        is_pg = db.engine.dialect.name != 'sqlite'
        bool_default = 'FALSE' if is_pg else '0'
        # Postgres DATETIME type is not valid — must use TIMESTAMP.
        datetime_type = 'TIMESTAMP' if is_pg else 'DATETIME'
        with db.engine.begin() as conn:
            if 'is_deleted' not in cols:
                conn.execute(text(f'ALTER TABLE campaigns ADD COLUMN is_deleted BOOLEAN DEFAULT {bool_default}'))
            if 'deleted_at' not in cols:
                conn.execute(text(f'ALTER TABLE campaigns ADD COLUMN deleted_at {datetime_type}'))
            if 'deleted_by' not in cols:
                conn.execute(text('ALTER TABLE campaigns ADD COLUMN deleted_by INTEGER'))

    if 'tasks' in tables:
        cols = {c['name'] for c in inspector.get_columns('tasks')}
        if 'cancel_reason' not in cols:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE tasks ADD COLUMN cancel_reason VARCHAR(255)'))

if app.config.get('RUN_STARTUP_SCHEMA_MAINTENANCE'):
    with app.app_context():
        try:
            db.create_all()
            _create_missing_indexes()
        except Exception as _mig_err:
            logger.exception('Explicit startup schema maintenance failed: %s', _mig_err)
            raise


# ----------------- Domain helpers -----------------

def get_or_create_wallet(user_id, flush=False):
    wallet = Wallet.query.filter_by(user_id=user_id).with_for_update().first()
    if not wallet:
        wallet = Wallet(user_id=user_id, total_points=0, available_points=0, redeemed_points=0)
        db.session.add(wallet)
        if flush:
            db.session.flush()
    return wallet


def current_month_task_count(user_id, now=None):
    now = now or utc_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = month_start.replace(year=now.year + 1, month=1)
    else:
        next_month = month_start.replace(month=now.month + 1)
    return Task.query.filter(
        Task.user_id == user_id,
        Task.status != 'Cancelled',
        Task.assigned_date >= month_start,
        Task.assigned_date < next_month
    ).count()


def assign_one_campaign_task(user, campaign, now=None):
    """Assign at most one task for this campaign/user/month, respecting the campaign pool and 14-task monthly cap."""
    now = now or utc_now()
    if user.is_blocked or campaign.status != 'Active' or campaign.is_deleted:
        return None
    if campaign.end_date and campaign.end_date < now.date():
        return None

    if current_month_task_count(user.id, now) >= 14:
        return None

    already_assigned = UserCampaignTaskAssignment.query.filter(
        UserCampaignTaskAssignment.user_id == user.id,
        UserCampaignTaskAssignment.campaign_id == campaign.id,
        UserCampaignTaskAssignment.assigned_at >= now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        UserCampaignTaskAssignment.assigned_at < (
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(
                year=now.year + 1, month=1
            ) if now.month == 12 else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(month=now.month + 1)
        )
    ).first()
    if already_assigned:
        return None

    progress = CampaignAllocationProgress.query.filter_by(campaign_id=campaign.id).with_for_update().first()
    if not progress:
        return None

    planned = progress.total_tasks_planned or progress.total_tasks_created or 0
    assigned = progress.total_tasks_assigned or 0
    if assigned >= planned:
        progress.is_fully_allocated = True
        return None

    review_texts = CampaignReviewText.query.filter_by(campaign_id=campaign.id).all()
    if not review_texts:
        logger.error('Campaign %s has no review text; assignment skipped.', campaign.id)
        return None
    review_text = random.choice(review_texts).review_text

    task = Task(
        campaign_id=campaign.id,
        user_id=user.id,
        review_text=review_text,
        gmb_link=campaign.gmb_link,
        status='Assigned',
        assigned_date=now,
        points_per_review=campaign.points_per_review or 0,
        allocation_month=1
    )
    db.session.add(task)
    db.session.flush()

    assignment = UserCampaignTaskAssignment(
        user_id=user.id,
        campaign_id=campaign.id,
        task_id=task.id,
        status='Assigned',
        assigned_at=now
    )
    db.session.add(assignment)

    progress.total_tasks_assigned = assigned + 1
    progress.total_tasks_created = (progress.total_tasks_created or 0) + 1
    progress.users_count_assigned = (progress.users_count_assigned or 0) + 1
    progress.is_fully_allocated = progress.total_tasks_assigned >= planned
    progress.updated_at = now

    user.total_tasks_assigned = (user.total_tasks_assigned or 0) + 1
    user.last_allocation_date = now
    user.calculate_priority()
    return task


def sync_user_legacy_points(user, wallet):
    # User.total_points/redeemed_points are legacy columns kept for compatibility.
    # Wallet is the source of truth, but we keep these mirrors synchronized.
    user.total_points = wallet.total_points or 0
    user.redeemed_points = wallet.redeemed_points or 0


def notify_task_assigned(user, campaign):
    db.session.add(Notification(
        user_id=user.id,
        message=f'🎉 New task assigned for campaign: {campaign.name}',
        notification_type='task_assigned'
    ))


# ----------------- USER ROUTES -----------------
    
@app.route("/")
def index():
    # Agar user already logged in hai to seedha dashboard
    if 'user_id' in session:
        return redirect(url_for('user_dashboard'))
    elif 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    
    # Agar PWA App se website open ho rahi hai, to seedha login par bhej do
    if request.args.get('source') == 'pwa':
        return redirect(url_for('user_login'))

    # Normal website visitors ke liye landing page (index.html) bina cache ke dikhao
    resp = make_response(render_template("index.html"))
    
    # BROWSER CACHE FIX (Ye mobile browser ko purana page yaad rakhne se rokega)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    
    return resp

@app.route("/landing")
def landing():
    # If landing page is still needed externally
    return render_template("index.html")


@app.route("/register", methods=["GET"])
def user_register():
    return render_template("register.html")


@app.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.form
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()

    if not email or not password or not name or not phone:
        flash("❌ All fields including Mobile Number are required!", "error")
        return redirect(url_for('user_register'))

    valid, msg = is_valid_email(email)
    if not valid:
        flash(f"❌ {msg}", "error")
        return redirect(url_for('user_register'))
    valid, msg = is_valid_name(name)
    if not valid:
        flash(f"❌ {msg}", "error")
        return redirect(url_for('user_register'))
    valid, msg = is_valid_password(password)
    if not valid:
        flash(f"❌ {msg}", "error")
        return redirect(url_for('user_register'))
    valid, msg = is_valid_phone(phone)
    if not valid:
        flash(f"❌ {msg}", "error")
        return redirect(url_for('user_register'))

    try:
        if User.query.filter_by(email=email).first():
            flash("❌ Email already exists!", "error")
            return redirect(url_for('user_register'))

        user = User(email=email, name=name, mobile=phone)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        db.session.add(Wallet(user_id=user.id, total_points=0, available_points=0, redeemed_points=0))

        now = utc_now()
        tasks_assigned_count = 0
        active_campaigns = Campaign.query.filter_by(status='Active', is_deleted=False).with_for_update().all()
        for campaign in active_campaigns:
            task = assign_one_campaign_task(user, campaign, now)
            if task:
                notify_task_assigned(user, campaign)
                tasks_assigned_count += 1

        if tasks_assigned_count:
            db.session.add(Notification(
                user_id=user.id,
                message=f'🎉 Welcome! You have {tasks_assigned_count} task(s) to complete and earn points! 💰',
                notification_type='auto_assigned',
                is_read=False
            ))

        db.session.commit()
        flash("✅ Registration successful! Tasks assigned according to the monthly allocation rules. 🎉", "success")
        return redirect(url_for('user_login'))

    except IntegrityError:
        db.session.rollback()
        flash("❌ Email already exists or account could not be created. Please try again.", "error")
        return redirect(url_for('user_register'))
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error finalizing registration: {e}")
        flash('❌ Error saving account. Please try again.', 'error')
        return redirect(url_for('user_register'))


# [OTP BYPASSED] - This route remains in the code but won't be used since OTP is stopped.
@app.route("/verify_registration", methods=["GET", "POST"])
def verify_registration():
    if 'pending_registration' not in session:
        flash('❌ Invalid request, please register again.', 'error')
        return redirect(url_for('user_register'))

    reg_data = session['pending_registration']
    email = reg_data['email']

    if request.method == "POST":
        entered_otp = request.form.get('otp', '').strip()
        current_time = utc_now().replace(tzinfo=timezone.utc).timestamp()

        if entered_otp != reg_data['otp'] or current_time > reg_data['expires']:
            flash('❌ Invalid or expired OTP!', 'error')
            return render_template('verify_registration.html', email=email)

        # Legacy logic (moved to register POST)
        pass 

    return render_template('verify_registration.html', email=email)


# ----------------- LOGIN / DASHBOARD -----------------

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def user_login():
    if request.method == "POST":
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('❌ Email and password required!', 'error')
            return render_template("login.html")

        # =====================================================
        # 1. ADMIN LOGIN
        # =====================================================
        if (
            email == app.config.get('ADMIN_EMAIL', '').strip().lower()
            and check_admin_password(password)
        ):
            # Remove any previous user/admin session data
            session.clear()

            session['admin_id'] = 1
            session['admin_email'] = email
            session['admin_credential_fingerprint'] = _ADMIN_CREDENTIAL_FINGERPRINT

            # Admin cookies expire with the browser session.
            session.permanent = False

            flash('✅ Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))

        # =====================================================
        # 2. USER LOGIN
        # =====================================================
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):

            # Blocked account check
            if getattr(user, 'is_blocked', False):
                flash(
                    '❌ Your account has been blocked by Admin. '
                    'Contact support.',
                    'error'
                )
                return render_template("login.html")

            # -------------------------------------------------
            # Create fresh user session
            # -------------------------------------------------
            session.clear()

            session['user_id'] = user.id
            session['user_email'] = user.email
            session['user_name'] = user.name
            session['password_fingerprint'] = password_fingerprint(user)

            # IMPORTANT:
            # Keep the login session persistent.
            session.permanent = True

            flash('✅ Login successful!', 'success')
            return redirect(url_for('user_dashboard'))

        # =====================================================
        # 3. INVALID LOGIN
        # =====================================================
        flash('❌ Invalid email or password!', 'error')

    return render_template("login.html")

@app.route("/dashboard")
@login_required
def user_dashboard():
    user = db.session.get(User, session.get('user_id'))

    if not user:
        session.clear()
        flash('❌ Session expired or user not found. Please login again.', 'error')
        return redirect(url_for('user_login'))

    wallet = Wallet.query.filter_by(user_id=user.id).first()
    tasks = Task.query.filter_by(user_id=user.id).all()

    # ✅ FIX: "Available Campaigns" me sirf wahi campaign dikhni chahiye jiska
    # is user ke paas abhi bhi koi ACTIONABLE (Assigned/Rejected) task hai.
    # Pehle yahan sirf Task.user_id check hota tha (status ignore), isliye
    # Approved (completed) task waali campaign bhi list me atki rehti thi.
    # Delete/cleared task automatically bahar ho jata hai kyunki row hi
    # exist nahi karti.
    #
    # ✅ FIX (Pause/Stop bug): Pehle yahan `Campaign.status == 'Active'` bhi
    # check hota tha, isliye jaise hi admin campaign PAUSE karta tha, us
    # campaign ka card yahan se turant gayab ho jata tha — jabki user ka
    # task abhi bhi 'Assigned' tha aur wo /tasks page se ussey submit kar
    # sakta tha (bas dashboard par dikhna band ho gaya tha, refresh karte
    # hi "gayab" jaisa lagta tha). Ab Paused campaign ka bhi card yahan
    # dikhega jab tak uska task Assigned/Rejected hai. Stopped/Deleted
    # campaign ke incomplete tasks automatically 'Cancelled' ho jate hain
    # (dekho _cancel_incomplete_campaign_tasks), isliye wo apne aap yahan
    # se hat jayenge — extra status check ki zaroorat nahi.
    campaigns = (
        Campaign.query
        .join(Task, Task.campaign_id == Campaign.id)
        .filter(
            Task.user_id == user.id,
            Task.status.in_(['Assigned', 'Rejected']),
            Campaign.is_deleted.is_(False)
        )
        .distinct()
        .all()
    )

    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t.status == 'Approved'])
    pending_tasks = len([t for t in tasks if t.status == 'Submitted'])

    return render_template("dashboard.html",
                         user=user,
                         wallet=wallet,
                         total_tasks=total_tasks,
                         completed_tasks=completed_tasks,
                         pending_tasks=pending_tasks,
                         campaigns=campaigns)


# ----------------- TASKS -----------------

@app.route("/tasks")
@login_required
def view_tasks():
    user_id = session.get('user_id')

    status_order = db.case(
        (Task.status == 'Assigned', 0),
        (Task.status == 'Rejected', 0),
        (Task.status == 'Submitted', 1),
        (Task.status == 'Approved', 2),
        (Task.status == 'Cancelled', 2),
        else_=3
    )
    
    tasks = (
        Task.query
        .outerjoin(Campaign, Campaign.id == Task.campaign_id)
        .filter(Task.user_id == user_id)
        .filter(
            or_(
                Campaign.id == None,           
                Campaign.is_deleted == False, 
                Task.status.in_(['Submitted', 'Approved']) 
            )
        )
        .order_by(status_order, Task.assigned_date.desc())
        .all()
    )
    
    return render_template("tasks.html", tasks=tasks)


@app.route("/tasks/clear", methods=["POST"])
@login_required
def clear_tasks():
    """✅ Naya feature: user apne Approved/Rejected/Cancelled (complete/history)
    tasks ki list se clear kar sake, taki mahino baad list chhoti/manageable
    rahe. Assigned/Submitted (jo abhi pending hai) kabhi clear nahi hote."""
    # Financial/task audit records must not be hard-deleted. A future schema
    # migration can add a per-user hidden_at flag without destroying history.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'status': 'error',
            'message': 'Task history is retained for account and wallet integrity.'
        }), 409

    flash('ℹ️ Task history is retained for account and wallet integrity.', 'info')
    return redirect(url_for('view_tasks'))


@app.route("/submit_task/<int:task_id>", methods=["POST"])
@login_required
def submit_task(task_id):
    user_id = session['user_id']
    task = db.session.query(Task).with_for_update().filter_by(id=task_id).first()

    if not task or task.user_id != user_id:
        flash('❌ Invalid task!', 'error')
        return redirect(url_for('view_tasks'))

    # Submitted/Approved tasks cannot be submitted again. Rejected tasks may be
    # resubmitted by replacing their existing one-to-one submission record.
    if task.status not in ('Assigned', 'Rejected'):
        flash('❌ This task is already submitted or approved.', 'error')
        return redirect(url_for('view_tasks'))

    file = request.files.get('screenshot')
    if not file or not file.filename:
        flash('❌ Please upload screenshot!', 'error')
        return redirect(url_for('view_tasks'))

    if not allowed_file(file.filename) or not is_valid_image_content(file):
        flash('❌ Invalid file format! Use a genuine PNG, JPG or JPEG image.', 'error')
        return redirect(url_for('view_tasks'))

    review_text_submitted = (request.form.get('review_text') or '').strip()
    if len(review_text_submitted) > 5000:
        flash('❌ Submitted review text is too long.', 'error')
        return redirect(url_for('view_tasks'))

    upload_path = None
    try:
        file.stream.seek(0)
        file_bytes = file.read()
        image = Image.open(BytesIO(file_bytes))
        image.load()
        output = BytesIO()
        if image.format == 'PNG':
            image.save(output, format='PNG', optimize=True)
            extension = '.png'
        else:
            image.convert('RGB').save(output, format='JPEG', quality=92, optimize=True)
            extension = '.jpg'
        safe_bytes = output.getvalue()
        screenshot_hash = hashlib.sha256(safe_bytes).hexdigest()

        duplicate = (
            TaskSubmission.query
            .join(Task, Task.id == TaskSubmission.task_id)
            .filter(
                TaskSubmission.screenshot_hash == screenshot_hash,
                TaskSubmission.task_id != task_id
            )
            .first()
        )
        if duplicate:
            flash('❌ This screenshot has already been submitted before! Please upload a new, unique screenshot.', 'error')
            return redirect(url_for('view_tasks'))

        filename = secure_filename(str(uuid.uuid4()) + extension)
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(upload_path, 'wb') as f:
            f.write(safe_bytes)

        submission = TaskSubmission.query.filter_by(task_id=task_id).first()
        old_filename = submission.screenshot_url if submission else None
        if not submission:
            submission = TaskSubmission(task_id=task_id)
            db.session.add(submission)

        submission.screenshot_url = filename
        submission.screenshot_hash = screenshot_hash
        submission.review_text_submitted = review_text_submitted or None
        submission.submitted_date = utc_now()
        submission.verification_status = 'Pending'
        submission.admin_notes = None
        submission.verified_date = None
        submission.verified_by = None

        task.status = 'Submitted'
        task.submission_date = utc_now()

        assignment = UserCampaignTaskAssignment.query.filter_by(task_id=task_id).first()
        if assignment:
            assignment.status = 'Submitted'
            assignment.completed_at = None

        db.session.commit()

        if old_filename and old_filename != filename:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_filename)
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except OSError:
                logger.warning('Could not remove old screenshot %s', old_filename)

        user = db.session.get(User, user_id)
        notify_admins('new_task_submission', {
            'message': f'📩 New task submission from {user.name if user else "a student"} (Task #{task_id})',
            'task_id': task_id,
            'submission_id': submission.id
        })
        flash('✅ Task submitted successfully!', 'success')
        return redirect(url_for('view_tasks'))
    except (SQLAlchemyError, OSError, ValueError):
        db.session.rollback()
        if upload_path and os.path.isfile(upload_path):
            try:
                os.remove(upload_path)
            except OSError:
                logger.warning('Could not remove failed upload %s', upload_path)
        flash('❌ Could not submit task. Please try again.', 'error')
        return redirect(url_for('view_tasks'))


# ----------------- WALLET / REDEEM -----------------

@app.route("/wallet")
@login_required
def wallet():
    user_id = session['user_id']
    wallet = Wallet.query.filter_by(user_id=user_id).first()

    if not wallet:
        wallet = Wallet(user_id=user_id, total_points=0, available_points=0, redeemed_points=0)
        db.session.add(wallet)
        db.session.commit()

    transactions = (WalletTransaction.query.filter_by(wallet_id=wallet.id)
                    .order_by(WalletTransaction.transaction_date.desc()).limit(200).all())
    redeem_requests = (RedeemRequest.query.filter_by(user_id=user_id)
                       .order_by(RedeemRequest.requested_at.desc()).limit(100).all())

    return render_template("wallet.html",
                         wallet=wallet,
                         transactions=transactions,
                         redeem_requests=redeem_requests)


@app.route("/redeem", methods=["POST"])
@login_required
def redeem_points():
    user_id = session['user_id']
    try:
        points = int(request.form.get('points', 0))
    except (TypeError, ValueError):
        flash('❌ Invalid points amount!', 'error')
        return redirect(url_for('wallet'))

    payment_method = (request.form.get('payment_method') or '').strip()
    payment_details = (request.form.get('payment_details') or '').strip()
    allowed_payment_methods = {'UPI', 'Paytm', 'Phone Pay', 'Google Pay'}

    if points < 500:
        flash('❌ Minimum 500 points required to redeem! (500 Points = ₹500)', 'error')
        return redirect(url_for('wallet'))
    if payment_method not in allowed_payment_methods or not payment_details:
        flash('❌ Payment method and details are required!', 'error')
        return redirect(url_for('wallet'))
    if len(payment_method) > 50 or len(payment_details) > 64:
        flash('❌ Payment details are too long.', 'error')
        return redirect(url_for('wallet'))
    valid_payment_detail = bool(
        re.fullmatch(r'[0-9]{10}', payment_details)
        or re.fullmatch(r'[A-Za-z0-9._-]{2,}@[A-Za-z0-9.-]{2,}', payment_details)
    )
    if not valid_payment_detail:
        flash('❌ Enter a valid 10-digit mobile number or UPI ID.', 'error')
        return redirect(url_for('wallet'))

    try:
        wallet = Wallet.query.filter_by(user_id=user_id).with_for_update().first()
        if not wallet:
            flash('❌ Wallet not found!', 'error')
            return redirect(url_for('wallet'))
        if points > wallet.available_points:
            flash('❌ Insufficient points!', 'error')
            return redirect(url_for('wallet'))

        redeem_req = RedeemRequest(
            user_id=user_id,
            points=points,
            payment_method=payment_method,
            payment_details=encrypt_sensitive(payment_details),
            status='In Process'
        )
        db.session.add(redeem_req)
        db.session.flush()

        wallet.available_points -= points
        wallet.redeemed_points += points
        sync_user_legacy_points(db.session.get(User, user_id), wallet)

        db.session.add(WalletTransaction(
            wallet_id=wallet.id,
            transaction_type='Redeem',
            points=points,
            reference_id=f"REDEEM_{redeem_req.id}"
        ))
        db.session.commit()

        user = db.session.get(User, user_id)
        notify_admins('new_redeem_request', {
            'message': f'💰 New redeem request from {user.name if user else "a student"} for {points} points',
            'redeem_id': redeem_req.id
        })
        flash('✅ Redeem request submitted! 1 Point = ₹1 | Conversion Rate: 1:1', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('❌ This redeem request could not be created. Please try again.', 'error')
    except SQLAlchemyError:
        db.session.rollback()
        flash('❌ Database error while creating redeem request.', 'error')
    return redirect(url_for('wallet'))


# ----------------- AUTH / LOGOUT -----------------

@app.route("/logout")
def logout():
    session.clear()
    flash('✅ Logout successful!', 'success')
    return redirect(url_for('index'))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash('✅ Admin logout successful!', 'success')
    return redirect(url_for('index'))


# ----------------- NOTIFICATIONS -----------------

@app.route('/api/notifications')
@login_required
def get_notifications():
    user_id = session['user_id']
    notifications = (Notification.query.filter_by(user_id=user_id)
                     .order_by(Notification.created_at.desc()).limit(100).all())
    unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()

    notifications_data = []
    for notif in notifications:
        notifications_data.append({
            'id': notif.id,
            'message': notif.message,
            'type': notif.notification_type,
            'is_read': notif.is_read,
            # ✅ FIX: Pehle '%d-%m-%Y %H:%M' (e.g. "19-08-2026 14:30") bhej rahe
            # the, jisko JS ka `new Date(...)` reliably parse nahi kar pata
            # (Invalid Date) — isi wajah se "NaN years/seconds ago" dikh raha
            # tha. ISO-8601 + 'Z' (UTC marker) bhejne se `new Date()` sahi
            # se parse karta hai, chahe browser koi bhi ho.
            'created_at': iso_utc(notif.created_at),
            'related_id': notif.related_id
        })

    return jsonify({
        'notifications': notifications_data,
        'unread_count': unread_count
    })


@app.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    notification = db.session.get(Notification, notif_id)
    if not notification or notification.user_id != session['user_id']:
        return jsonify({'status': 'error', 'message': 'Notification not found'}), 404

    notification.is_read = True
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Marked as read'})


@app.route('/api/notifications/<int:notif_id>/delete', methods=['POST'])
@login_required
def delete_notification(notif_id):
    """✅ Naya feature: user single notification delete kar sake."""
    notification = db.session.get(Notification, notif_id)
    if not notification or notification.user_id != session['user_id']:
        return jsonify({'status': 'error', 'message': 'Notification not found'}), 404

    db.session.delete(notification)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Notification deleted'})


@app.route('/api/notifications/clear', methods=['POST'])
@login_required
def clear_notifications():
    """✅ Naya feature: user apni saari notifications ek click me clear kar
    sake (jaise /tasks/clear pehle se history clear karta hai)."""
    user_id = session['user_id']
    deleted = Notification.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success', 'deleted': deleted})

    flash(f'✅ {deleted} notifications cleared!', 'success')
    return redirect(url_for('view_notifications'))


@app.route('/notifications')
@login_required
def view_notifications():
    user_id = session['user_id']
    notifications = (Notification.query.filter_by(user_id=user_id)
                     .order_by(Notification.created_at.desc()).limit(200).all())
    return render_template('notifications.html', notifications=notifications)


# ----------------- SUPPORT -----------------

@app.route('/support')
@login_required
def support_page():
    user_id = session['user_id']
    tickets = SupportTicket.query.filter_by(user_id=user_id).order_by(SupportTicket.created_at.desc()).all()
    return render_template('support.html', tickets=tickets)


@app.route('/support/create', methods=['POST'])
@login_required
def create_support_ticket():
    user_id = session['user_id']
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()
    category = request.form.get('category', 'other').strip()
    priority = request.form.get('priority', 'Normal').strip()

    if not subject or not message:
        flash('❌ Subject and message are required!', 'error')
        return redirect(url_for('support_page'))

    allowed_categories = {'task_help', 'payment', 'account', 'redeem', 'other'}
    allowed_priorities = {'Low', 'Normal', 'High', 'Urgent'}
    if category not in allowed_categories:
        category = 'other'
    if priority not in allowed_priorities:
        priority = 'Normal'

    if len(subject) < 5 or len(subject) > 200 or len(message) < 10 or len(message) > 5000:
        flash('❌ Check length of subject/message!', 'error')
        return redirect(url_for('support_page'))

    ticket = SupportTicket(
        user_id=user_id, subject=subject, message=message,
        category=category, priority=priority, status='Open'
    )
    db.session.add(ticket)
    db.session.flush()
    admin_notification = Notification(
        user_id=None,
        message=f'New support ticket from {db.session.get(User, user_id).name}: {subject}',
        notification_type='support',
        related_id=ticket.id,
        is_read=False
    )
    db.session.add(admin_notification)
    db.session.commit()

    notify_admins('new_support_ticket', {
        'message': f'🎫 New support ticket: {subject}',
        'ticket_id': ticket.id
    })

    flash('✅ Support ticket created successfully!', 'success')
    return redirect(url_for('support_page'))


@app.route('/support/ticket/<int:ticket_id>')
@login_required
def view_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket or ticket.user_id != session['user_id']:
        flash('❌ Ticket not found!', 'error')
        return redirect(url_for('support_page'))
    return render_template('support_ticket.html', ticket=ticket)


@app.route('/support/ticket/<int:ticket_id>/reply', methods=['POST'])
@login_required
def reply_ticket(ticket_id):
    user_id = session['user_id']
    ticket = db.session.get(SupportTicket, ticket_id)

    if not ticket or ticket.user_id != user_id:
        return jsonify({'status': 'error', 'message': 'Ticket not found'}), 404
    if ticket.status == 'Closed':
        return jsonify({'status': 'error', 'message': 'Ticket is closed'}), 400

    message = request.form.get('message', '').strip()
    if not message or len(message) < 5 or len(message) > 5000:
        return jsonify({'status': 'error', 'message': 'Message must be between 5 and 5000 characters'}), 400

    reply = SupportReply(
        ticket_id=ticket_id, user_id=user_id, message=message, is_admin_reply=False
    )
    db.session.add(reply)
    ticket.updated_at = utc_now()
    db.session.commit()

    reply_data = {
        'id': reply.id, 'message': reply.message,
        'created_at': reply.created_at.strftime('%d-%m-%Y %H:%M'),
        'user_name': db.session.get(User, user_id).name,
        'is_admin': False,
        'ticket_id': ticket_id
    }
    socketio.emit('ticket_reply', reply_data, room=f'ticket_{ticket_id}')
    notify_admins('ticket_activity', {'ticket_id': ticket_id, 'message': f'💬 New reply on ticket #{ticket_id}'})

    return jsonify({
        'status': 'success', 'message': 'Reply added successfully',
        'reply': reply_data
    })


# ----------------- ADMIN ROUTES -----------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    # Admin login unified, redirect to normal user login page
    return redirect(url_for('user_login'))


@app.route("/admin")
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_campaigns = Campaign.query.filter_by(is_deleted=False).count()
    active_campaigns = Campaign.query.filter_by(status='Active', is_deleted=False).count()
    pending_tasks = TaskSubmission.query.filter_by(verification_status='Pending').count()
    pending_redeems = RedeemRequest.query.filter_by(status='In Process').count()
    pending_support = SupportTicket.query.filter(SupportTicket.status.in_(['Open', 'In Progress'])).count()

    return render_template("admin_dashboard.html",
                         total_users=total_users,
                         total_campaigns=total_campaigns,
                         active_campaigns=active_campaigns,
                         pending_tasks=pending_tasks,
                         pending_redeems=pending_redeems,
                         pending_support=pending_support)


@app.route("/admin/create_campaign", methods=["GET", "POST"])
@admin_required
def create_campaign():
    if request.method == "POST":
        name = request.form.get('name', '').strip()
        gmb_link = request.form.get('gmb_link', '').strip()

        try:
            total_reviews = int(request.form.get('total_reviews', 0))
            points_per_review = int(request.form.get('points_per_review', 0))
            duration_months = int(request.form.get('duration_months', 1))
        except ValueError:
            flash('❌ Invalid values! All numeric fields must be numbers.', 'error')
            return render_template("admin_create_campaign.html")

        if (
            not name or len(name) > 200 or not gmb_link or len(gmb_link) > 500
            or not is_safe_http_url(gmb_link)
            or total_reviews <= 0 or total_reviews > 100_000
            or points_per_review <= 0 or points_per_review > 100_000
            or duration_months <= 0 or duration_months > 120
        ):
            flash('❌ Check all required fields.', 'error')
            return render_template("admin_create_campaign.html")

        review_texts = request.form.get('review_texts', '').split('\n')
        review_texts = [t.strip() for t in review_texts if t.strip()]
        if not review_texts or len(review_texts) > 10_000 or any(len(t) > 1000 for t in review_texts):
            flash('❌ At least one review text is required!', 'error')
            return render_template("admin_create_campaign.html")

        start_date = datetime.now().date()
        end_date = start_date + timedelta(days=30*duration_months)

        campaign = Campaign(
            name=name, gmb_link=gmb_link, total_reviews_required=total_reviews,
            points_per_review=points_per_review, start_date=start_date,
            end_date=end_date, duration_months=duration_months,
            status='Active', created_by=session['admin_id']
        )
        db.session.add(campaign)
        db.session.flush()

        for review_text in review_texts:
            text_obj = CampaignReviewText(campaign_id=campaign.id, review_text=review_text)
            db.session.add(text_obj)
        db.session.commit()

        notify_admins('new_campaign', {
            'message': f'📢 New campaign created: {campaign.name}',
            'campaign_id': campaign.id
        })

        flash('✅ Campaign created successfully! Now allocate tasks.', 'success')
        return redirect(url_for('admin_campaigns'))

    return render_template("admin_create_campaign.html")


@app.route("/admin/campaigns")
@admin_required
def admin_campaigns():
    # ✅ FIX: Soft-deleted campaigns admin ki list me kabhi nahi dikhengi,
    # lekin unka data DB me surakshit rehta hai (delete_campaign() dekho).
    campaigns = Campaign.query.filter(Campaign.is_deleted.is_(False)).all()
    return render_template("admin_campaigns.html", campaigns=campaigns)


def _cancel_incomplete_campaign_tasks(campaign, reason, now=None):
    """✅ FIX: Campaign Stop/Delete hone par jo tasks abhi tak user ne
    complete/submit NAHI kiye (status Assigned/Rejected), unhe 'Cancelled'
    mark karo — ek chhoti si note (cancel_reason) ke saath — taaki:
      1) User ko dashboard/tasks page par pata chale ke wo task ab kyun
         nahi kar sakta (uski galti nahi thi, campaign band ho gayi).
      2) Wo task kisi naye user ko allocate na ho (naya assignment already
         campaign.status != 'Active' check ki wajah se rukta hai — ye sirf
         PURANE, already-assigned-par-incomplete tasks ko close karta hai).
      3) User ki fairness/priority history (total_tasks_assigned) touch
         nahi hoti — ye uski galti se cancel nahi hua.
    Already Submitted/Approved tasks ko haath nahi lagaya jata — Submitted
    wale admin abhi bhi verify kar sakta hai, Approved wale complete hi hain.
    """
    now = now or utc_now()

    incomplete_tasks = Task.query.filter(
        Task.campaign_id == campaign.id,
        Task.status.in_(['Assigned', 'Rejected'])
    ).all()

    cancelled_count = 0
    for task in incomplete_tasks:
        task.status = 'Cancelled'
        task.cancel_reason = reason

        assignment = UserCampaignTaskAssignment.query.filter_by(task_id=task.id).first()
        if assignment:
            assignment.status = 'Cancelled'
            assignment.completed_at = now

        notify_user(
            task.user_id,
            f'⚠️ Task for campaign "{campaign.name}" was cancelled — {reason}',
            notification_type='task', related_id=task.id
        )
        cancelled_count += 1

    return cancelled_count


@app.route("/admin/campaign/<int:campaign_id>/<action>", methods=["POST"])
@admin_required
def campaign_action(campaign_id, action):
    campaign = db.session.query(Campaign).with_for_update().filter_by(id=campaign_id).first()
    if not campaign or campaign.is_deleted:
        flash('❌ Campaign not found!', 'error')
        return redirect(url_for('admin_campaigns'))

    if action not in ('pause', 'resume', 'stop'):
        flash('❌ Invalid action!', 'error')
        return redirect(url_for('admin_campaigns'))

    cancelled_count = 0
    if action == 'pause':
        # ✅ Paused = temporary. Existing "Assigned" tasks users ke paas
        # rehte hain (wo abhi bhi submit kar sakte hain), sirf NAYE tasks
        # allocate hona ruk jata hai (assign_one_campaign_task /
        # allocate_tasks dono campaign.status == 'Active' check karte hain).
        campaign.status = 'Paused'
    elif action == 'resume':
        if campaign.end_date and campaign.end_date < utc_now().date():
            flash('❌ Expired campaigns cannot be resumed. Extend the duration first.', 'error')
            return redirect(url_for('admin_campaigns'))
        campaign.status = 'Active'
    elif action == 'stop':
        # ✅ Stopped = permanent. Ab koi naya task allocate nahi hoga, aur
        # jo purane tasks abhi tak incomplete the (user submit nahi kar
        # paya) unhe 'Cancelled' mark karke note add kar diya jata hai.
        campaign.status = 'Stopped'
        cancelled_count = _cancel_incomplete_campaign_tasks(
            campaign, reason='Campaign was stopped by admin before you could complete it.'
        )

    db.session.commit()
    action_word = {'pause': 'paused', 'resume': 'resumed', 'stop': 'stopped'}[action]
    notify_admins('campaign_updated', {
        'message': f'Campaign "{campaign.name}" {action_word}',
        'campaign_id': campaign.id,
        'action': action
    })

    if action == 'stop' and cancelled_count:
        flash(
            f'✅ Campaign stopped successfully! {cancelled_count} incomplete task(s) were '
            f'cancelled and marked in user history.', 'success'
        )
    else:
        flash(f'✅ Campaign {action_word} successfully!', 'success')
    return redirect(url_for('admin_campaigns'))


@app.route("/admin/campaign/<int:campaign_id>/delete", methods=["POST"])
@admin_required
def delete_campaign(campaign_id):
    """✅ FIX: Ye ab HARD delete nahi, SOFT delete karta hai.
    Campaign row DB se hata nahi ta — sirf is_deleted=True set hota hai,
    isliye:
      - Admin ki campaign list (admin_campaigns) se ye turant gayab ho
        jayega.
      - Campaign ka poora data (tasks, submissions, review texts,
        allocation progress, user assignment history) bilkul waisa hi
        DB me surakshit rehta hai — kuch bhi unlink/null/delete nahi hota.
      - User ki task history (Approved/Rejected/Cancelled) me campaign
        ka naam/link tab bhi sahi dikhta hai.
    """
    campaign = db.session.query(Campaign).with_for_update().filter_by(id=campaign_id).first()
    if not campaign or campaign.is_deleted:
        flash('❌ Campaign not found!', 'error')
        return redirect(url_for('admin_campaigns'))

    if campaign.created_by != session.get('admin_id'):
        flash('❌ You can only delete campaigns you created!', 'error')
        return redirect(url_for('admin_campaigns'))

    campaign_name = campaign.name

    # ✅ FIX: Delete se pehle har task ke points snapshot kar lo (agar
    # kisi purani row me abhi tak nahi set hue), taki campaign delete hone
    # ke baad bhi user ko sahi points dikhein (pehle 0 point bug tha).
    for t in Task.query.filter_by(campaign_id=campaign.id).all():
        if not t.points_per_review:
            t.points_per_review = campaign.points_per_review or 0

    # ✅ FIX: Delete se pehle jo tasks abhi tak incomplete the (user submit
    # nahi kar paya), unhe bhi Stop ki tarah 'Cancelled' + note mark karo —
    # taaki wo task na kisi user ke paas atka rahe, na kisi naye user ko
    # allocate ho (list se hi campaign hat jayegi, koi allocation possible
    # nahi rahega).
    cancelled_count = _cancel_incomplete_campaign_tasks(
        campaign, reason='Campaign was deleted by admin before you could complete it.'
    )

    campaign.is_deleted = True
    campaign.deleted_at = utc_now()
    campaign.deleted_by = session.get('admin_id')
    if campaign.status != 'Stopped':
        campaign.status = 'Stopped'

    db.session.commit()

    notify_admins('campaign_updated', {
        'message': f'Campaign "{campaign_name}" deleted',
        'action': 'delete'
    })

    msg = f'✅ Campaign "{campaign_name}" deleted successfully! User task history preserved.'
    if cancelled_count:
        msg += f' {cancelled_count} incomplete task(s) were cancelled.'
    flash(msg, 'success')
    return redirect(url_for('admin_campaigns'))


@app.route("/admin/campaign/<int:campaign_id>/edit", methods=["POST"])
@admin_required
def edit_campaign(campaign_id):
    """✅ Naya feature: campaign create hone ke baad admin sirf 2 cheezein
    edit kar sake -
      1) Total users/tasks (total_reviews_required) - SIRF BADHA sakta hai,
         taaki already-created tasks/allocation-progress se conflict na ho.
      2) Duration (duration_months) - end_date start_date se dobara
         calculate ho jata hai.
    Baaki fields (name, gmb_link, points_per_review, review texts) is route
    se edit nahi hote - scope jaan-bujh kar chhota rakha gaya hai.
    """
    campaign = db.session.query(Campaign).with_for_update().filter_by(id=campaign_id).first()
    if not campaign:
        flash('❌ Campaign not found!', 'error')
        return redirect(url_for('admin_campaigns'))

    if campaign.created_by != session.get('admin_id'):
        flash('❌ You can only edit campaigns you created!', 'error')
        return redirect(url_for('admin_campaigns'))

    if campaign.status == 'Stopped':
        flash('❌ Stopped campaigns cannot be edited.', 'error')
        return redirect(url_for('admin_campaigns'))

    try:
        new_total_reviews_required = int(request.form.get('total_reviews_required', 0))
        new_duration_months = int(request.form.get('duration_months', 0))
    except (TypeError, ValueError):
        flash('❌ Invalid values! Users count and duration must be numbers.', 'error')
        return redirect(url_for('admin_campaigns'))

    if new_duration_months <= 0 or new_duration_months > 120:
        flash('❌ Duration must be at least 1 month.', 'error')
        return redirect(url_for('admin_campaigns'))

    # ✅ GUARD: total_reviews_required sirf badhaya ja sakta hai, ghataya
    # nahi - ghatane se allocation math (planned vs created tasks) tut
    # sakta hai.
    if new_total_reviews_required < (campaign.total_reviews_required or 0):
        flash('❌ Total users/tasks can only be increased, not decreased.', 'error')
        return redirect(url_for('admin_campaigns'))

    # ✅ Double safety: kabhi bhi already-created tasks se kam na ho.
    progress = CampaignAllocationProgress.query.filter_by(campaign_id=campaign.id).first()
    already_created = (progress.total_tasks_created or 0) if progress else 0
    if new_total_reviews_required < already_created:
        flash(f'❌ Cannot set below {already_created} tasks already created for this campaign.', 'error')
        return redirect(url_for('admin_campaigns'))

    old_total = campaign.total_reviews_required
    old_duration = campaign.duration_months
    delta = new_total_reviews_required - (old_total or 0)

    campaign.total_reviews_required = new_total_reviews_required
    campaign.duration_months = new_duration_months
    if campaign.start_date:
        campaign.end_date = campaign.start_date + timedelta(days=30 * new_duration_months)

    # ✅ ROOT CAUSE FIX: Pehle sirf `total_reviews_required` (cap) badhta tha,
    # lekin actual "planned pool" (CampaignAllocationProgress.total_tasks_planned)
    # tab tak nahi badhta jab tak admin dobara "Allocate Tasks" button na
    # dabaye. Isi wajah se edit karne ke baad Task Allocations me kuch add
    # nahi hota tha dikhta.
    # Ab jab bhi users count badhta hai aur campaign Active hai, naye slots
    # (delta) turant allocate kar diye jaate hain - alag se "Allocate Tasks"
    # dabane ki zaroorat nahi.
    allocation_note = ''
    if delta > 0 and campaign.status == 'Active':
        result = perform_task_allocation(campaign, delta, utc_now())
        if result['ok']:
            allocation_note = f" {result['assigned']} new task(s) allocated right away."
            if result['pending'] > 0:
                allocation_note += f" {result['pending']} slot(s) pending — will auto-fill as new users register."
        else:
            # Cap badh gaya (safe), lekin abhi allocate nahi ho paya (e.g. no
            # review texts, ya koi eligible user nahi) - admin ko batao,
            # edit ko fail mat karo.
            allocation_note = f" ⚠️ Slots added but not yet assigned: {result['message']}"
    elif delta > 0:
        allocation_note = ' Campaign is not Active, so new slots will be assigned once it is resumed.'

    db.session.commit()

    notify_admins('campaign_updated', {
        'message': (
            f'Campaign "{campaign.name}" edited: users {old_total}→{new_total_reviews_required}, '
            f'duration {old_duration}→{new_duration_months} month(s)'
        ),
        'campaign_id': campaign.id,
        'action': 'edit'
    })

    flash(
        f'✅ Campaign "{campaign.name}" updated! Users: {new_total_reviews_required}, '
        f'Duration: {new_duration_months} month(s).{allocation_note}',
        'success'
    )
    return redirect(url_for('admin_campaigns'))


@app.route("/admin/verify_tasks")
@admin_required
def verify_tasks():
    submissions = TaskSubmission.query.filter_by(verification_status='Pending').all()
    return render_template("admin_verify_tasks.html", submissions=submissions)


@app.route("/admin/task/<int:submission_id>/<action>", methods=["POST"])
@admin_required
def task_verify_action(submission_id, action):
    submission = db.session.query(TaskSubmission).with_for_update().filter_by(id=submission_id).first()
    if not submission:
        return jsonify({'status': 'error', 'message': 'Submission not found'}), 404
    task = submission.task
    user = task.user if task else None
    if not task or not user:
        return jsonify({'status': 'error', 'message': 'User associated with this task not found or deleted!'}), 404
    if action not in ('approve', 'reject'):
        return jsonify({'status': 'error', 'message': 'Invalid action'}), 400

    try:
        # Only a currently pending submission can be verified. This makes the
        # operation idempotent and prevents approve->approve/reject->reject races.
        if submission.verification_status != 'Pending' or task.status != 'Submitted':
            return jsonify({
                'status': 'error',
                'message': f'Submission is already {submission.verification_status.lower()} or task is not pending.'
            }), 409

        now = utc_now()
        submission.verified_date = now
        submission.verified_by = session.get('admin_id')

        assignment = UserCampaignTaskAssignment.query.filter_by(task_id=task.id).with_for_update().first()

        if action == 'approve':
            points = int(task.points_per_review or (task.campaign.points_per_review if task.campaign else 0) or 0)
            if points < 0:
                return jsonify({'status': 'error', 'message': 'Invalid task points configuration.'}), 400

            wallet = Wallet.query.filter_by(user_id=user.id).with_for_update().first()
            if not wallet:
                wallet = Wallet(user_id=user.id, total_points=0, available_points=0, redeemed_points=0)
                db.session.add(wallet)
                db.session.flush()

            # Idempotency guard even if the request somehow bypasses the status check.
            existing_tx = WalletTransaction.query.filter_by(reference_id=f"TASK_{task.id}").first()
            if existing_tx:
                return jsonify({'status': 'error', 'message': 'This task has already been credited.'}), 409

            wallet.total_points = (wallet.total_points or 0) + points
            wallet.available_points = (wallet.available_points or 0) + points
            sync_user_legacy_points(user, wallet)
            db.session.add(WalletTransaction(
                wallet_id=wallet.id,
                transaction_type='Earn',
                points=points,
                reference_id=f"TASK_{task.id}"
            ))

            submission.verification_status = 'Approved'
            task.status = 'Approved'
            if assignment:
                assignment.status = 'Approved'
                assignment.completed_at = now

            if task.campaign_id:
                progress = CampaignAllocationProgress.query.filter_by(campaign_id=task.campaign_id).with_for_update().first()
                if progress:
                    progress.total_tasks_completed = min(
                        (progress.total_tasks_completed or 0) + 1,
                        progress.total_tasks_assigned or progress.total_tasks_planned or 0
                    )
                    progress.updated_at = now
                    progress.is_fully_allocated = (progress.total_tasks_assigned or 0) >= (progress.total_tasks_planned or 0)
                    progress.users_count_completed = min(
                        (progress.users_count_completed or 0) + 1,
                        progress.users_count_assigned or progress.total_tasks_assigned or 0
                    )

            message = f'✅ Task approved! +{points} points credited.'
            extra_data = {
                'action': action,
                'task_id': task.id,
                'submission_id': submission.id,
                'points_awarded': points,
                'new_total_points': wallet.total_points,
                'new_available_points': wallet.available_points
            }
        else:
            submission.verification_status = 'Rejected'
            task.status = 'Rejected'
            if assignment:
                assignment.status = 'Rejected'
                assignment.completed_at = None
            message = '❌ Task rejected! You can submit a new screenshot for this task.'
            extra_data = {'action': action, 'task_id': task.id, 'submission_id': submission.id}

        notify_user(
            user.id, message,
            notification_type='task_approved' if action == 'approve' else 'task_rejected',
            related_id=task.id,
            extra=extra_data
        )
        db.session.commit()
        notify_admins('task_verified', {
            'message': f'Task #{task.id} {action}d',
            'task_id': task.id,
            'submission_id': submission.id,
            'action': action
        })
        if action == 'approve':
            broadcast_leaderboard_update()

        return jsonify({'status': 'success', 'message': message})
    except IntegrityError:
        db.session.rollback()
        logger.warning('Duplicate wallet transaction prevented for task %s', task.id)
        return jsonify({'status': 'error', 'message': 'This task has already been credited.'}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.exception(f"Database error while verifying task {submission_id}: {e}")
        return jsonify({'status': 'error', 'message': 'Database error occurred during transaction.'}), 500


@app.route("/admin/redeem/<int:redeem_id>/<action>", methods=["POST"])
@admin_required
def process_redeem(redeem_id, action):
    redeem_req = db.session.query(RedeemRequest).with_for_update().filter_by(id=redeem_id).first()
    if not redeem_req:
        return jsonify({'status': 'error', 'message': 'Redeem request not found'}), 404
    if action not in ('approve', 'reject'):
        return jsonify({'status': 'error', 'message': 'Invalid action'}), 400
    if redeem_req.status != 'In Process':
        return jsonify({'status': 'error', 'message': f'Redeem request is already {redeem_req.status.lower()}.'}), 409

    user = redeem_req.user
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    try:
        now = utc_now()
        if action == 'approve':
            redeem_req.status = 'Completed'
            redeem_req.processed_at = now
            redeem_req.processed_by = session['admin_id']
            message = f'✅ Redeem approved! {redeem_req.points} points transferred.'
            reject_message = None
        else:
            reason = ''
            if request.is_json:
                reason = ((request.get_json(silent=True) or {}).get('reason') or '').strip()
            else:
                reason = (request.form.get('reason') or '').strip()

            wallet = Wallet.query.filter_by(user_id=user.id).with_for_update().first()
            if not wallet:
                return jsonify({'status': 'error', 'message': 'Wallet not found for this user'}), 404
            if wallet.redeemed_points < redeem_req.points:
                return jsonify({'status': 'error', 'message': 'Wallet accounting mismatch; refund was not applied.'}), 409

            # Refund exactly once. The redeem request is already locked and status
            # is checked above, while this reference key protects against duplicate ledger writes.
            refund_ref = f"REFUND_{redeem_req.id}"
            if not WalletTransaction.query.filter_by(reference_id=refund_ref).first():
                wallet.available_points += redeem_req.points
                wallet.redeemed_points -= redeem_req.points
                sync_user_legacy_points(user, wallet)
                db.session.add(WalletTransaction(
                    wallet_id=wallet.id,
                    transaction_type='Refund',
                    points=redeem_req.points,
                    reference_id=refund_ref
                ))

            redeem_req.status = 'Failed'
            redeem_req.rejection_reason = reason or None
            redeem_req.processed_at = now
            redeem_req.processed_by = session['admin_id']
            reason_text = f" Reason: {reason}" if reason else ""
            reject_message = f"❌ Your redeem request for {redeem_req.points} points was rejected. Points refunded to your wallet.{reason_text}"
            message = '❌ Redeem rejected! Points refunded.'

        if action == 'approve':
            notify_user(
                user.id,
                f"🎉 Your redeem request for {redeem_req.points} points has been approved and processed!",
                notification_type='redeem_approved', related_id=redeem_req.id,
                extra={'points': redeem_req.points}
            )
        else:
            notify_user(
                user.id, reject_message, notification_type='redeem_rejected', related_id=redeem_req.id,
                extra={'points': redeem_req.points, 'reason': redeem_req.rejection_reason}
            )
        db.session.commit()

        notify_admins('redeem_processed', {
            'message': f'Redeem request #{redeem_req.id} {action}d',
            'redeem_id': redeem_req.id,
            'action': action
        })
        return jsonify({'status': 'success', 'message': message})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'This redeem request has already been processed.'}), 409
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'Database error occurred.'}), 500


@app.route("/admin/redeems")
@admin_required
def admin_redeems():
    redeems = RedeemRequest.query.all()
    return render_template("redeem_requests.html", redeems=redeems)


@app.route('/admin/list/users')
@admin_required
def users_list():
    users = User.query.all()
    return render_template("users_list.html", users=users)


@app.route('/admin/users/detailed')
@admin_required
def admin_users_detailed():
    users = User.query.all()
    task_totals = dict(
        db.session.query(Task.user_id, func.count(Task.id))
        .group_by(Task.user_id).all()
    )
    approved_totals = dict(
        db.session.query(Task.user_id, func.count(Task.id))
        .filter(Task.status == 'Approved').group_by(Task.user_id).all()
    )
    wallet_map = {wallet.user_id: wallet for wallet in Wallet.query.all()}

    user_data = []
    for user in users:
        wallet = wallet_map.get(user.id)
        user_data.append({
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'mobile': user.mobile,
            'tasks_completed': approved_totals.get(user.id, 0),
            'total_tasks': task_totals.get(user.id, 0),
            'points_earned': wallet.total_points if wallet else 0,
            'points_redeemed': wallet.redeemed_points if wallet else 0,
            'created_at': user.created_at.strftime('%d-%m-%Y') if user.created_at else '',
            'is_blocked': getattr(user, 'is_blocked', False)
        })

    return jsonify(user_data)


@app.route("/admin/list/<type>")
@admin_required
def admin_list(type):
    if type == "users":
        return render_template("partials/users.html", users=User.query.all())
    elif type == "campaigns":
        return render_template("partials/campaigns.html", campaigns=Campaign.query.filter_by(is_deleted=False).all())
    elif type == "tasks":
        return render_template("partials/tasks.html", tasks=TaskSubmission.query.filter_by(verification_status='Pending').all())
    elif type == "redeem_requests":
        return render_template("redeem_requests.html", redeems=RedeemRequest.query.all())
    return "Invalid Type"


def perform_task_allocation(campaign, tasks_to_plan_requested, now=None):
    """
    Core allocation logic (candidate select + Task rows create + progress
    update). Extracted from allocate_tasks() route so it can be reused from:
      1) /admin/allocate_tasks/<id>  (manual admin trigger)
      2) edit_campaign()             (auto-allocate the new slots right
                                       when admin increases total users)

    Returns a dict: {'ok': bool, 'message': str, 'assigned': int, 'pending': int}
    Caller is responsible for db.session.commit()/rollback() around this
    (matches with_for_update() row locks already taken by the caller).
    """
    now = now or utc_now()

    if tasks_to_plan_requested <= 0:
        return {'ok': False, 'message': 'Task count must be greater than zero.', 'assigned': 0, 'pending': 0}
    if campaign.is_deleted or campaign.status != 'Active':
        return {'ok': False, 'message': 'Campaign is not active.', 'assigned': 0, 'pending': 0}
    if campaign.end_date and campaign.end_date < now.date():
        return {'ok': False, 'message': 'Campaign has expired.', 'assigned': 0, 'pending': 0}

    review_texts = [r.review_text for r in CampaignReviewText.query.filter_by(campaign_id=campaign.id).all()]
    if not review_texts:
        return {'ok': False, 'message': 'No review texts available', 'assigned': 0, 'pending': 0}

    progress = db.session.query(CampaignAllocationProgress).with_for_update().filter_by(campaign_id=campaign.id).first()
    if not progress:
        progress = CampaignAllocationProgress(
            campaign_id=campaign.id,
            total_tasks_planned=0,
            total_tasks_created=0,
            total_tasks_assigned=0,
            users_count_assigned=0,
            total_tasks_completed=0,
            users_count_completed=0
        )
        db.session.add(progress)
        db.session.flush()

    planned = progress.total_tasks_planned or progress.total_tasks_created or 0
    campaign_cap = campaign.total_reviews_required or 0
    if campaign_cap and planned >= campaign_cap:
        return {'ok': False, 'message': 'Campaign task limit has already been reached.', 'assigned': 0, 'pending': 0}
    allowed_to_plan = campaign_cap - planned if campaign_cap else tasks_to_plan_requested
    tasks_to_plan = min(tasks_to_plan_requested, allowed_to_plan)
    if tasks_to_plan <= 0:
        return {'ok': False, 'message': 'No task slots remain in this campaign.', 'assigned': 0, 'pending': 0}

    # Each allocation request increases the campaign's planned pool. If fewer
    # eligible users are available now, registrations can consume the remaining slots later.
    progress.total_tasks_planned = planned + tasks_to_plan
    progress.users_count_planned = max(
        progress.users_count_planned or 0,
        progress.total_tasks_planned
    )

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start.replace(year=now.year + 1, month=1)
                  if now.month == 12 else month_start.replace(month=now.month + 1))

    assigned_this_month = db.session.query(UserCampaignTaskAssignment.user_id).filter(
        UserCampaignTaskAssignment.campaign_id == campaign.id,
        UserCampaignTaskAssignment.assigned_at >= month_start,
        UserCampaignTaskAssignment.assigned_at < next_month
    ).all()
    assigned_user_ids = {row[0] for row in assigned_this_month}

    monthly_counts = dict(
        db.session.query(Task.user_id, func.count(Task.id))
        .filter(
            Task.status != 'Cancelled',
            Task.assigned_date >= month_start,
            Task.assigned_date < next_month
        )
        .group_by(Task.user_id)
        .all()
    )

    # Historical fairness score + current month load. total_tasks_assigned is
    # incremented only when a task is actually assigned.
    candidates = User.query.filter(
        User.is_blocked.is_(False),
        ~User.id.in_(assigned_user_ids) if assigned_user_ids else True
    ).all()
    random.shuffle(candidates)
    candidates.sort(key=lambda u: ((u.total_tasks_assigned or 0) + monthly_counts.get(u.id, 0), u.id))

    selected = [u for u in candidates if monthly_counts.get(u.id, 0) < 14][:tasks_to_plan]

    for user in selected:
        task = Task(
            campaign_id=campaign.id,
            user_id=user.id,
            review_text=random.choice(review_texts),
            gmb_link=campaign.gmb_link,
            status='Assigned',
            assigned_date=now,
            points_per_review=campaign.points_per_review or 0,
            allocation_month=1
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(UserCampaignTaskAssignment(
            user_id=user.id,
            campaign_id=campaign.id,
            task_id=task.id,
            status='Assigned',
            assigned_at=now
        ))
        notify_task_assigned(user, campaign)
        user.total_tasks_assigned = (user.total_tasks_assigned or 0) + 1
        user.last_allocation_date = now
        user.calculate_priority()

    actual = len(selected)
    progress.total_tasks_created = (progress.total_tasks_created or 0) + actual
    progress.total_tasks_assigned = (progress.total_tasks_assigned or 0) + actual
    progress.users_count_assigned = (progress.users_count_assigned or 0) + actual
    progress.is_fully_allocated = progress.total_tasks_assigned >= progress.total_tasks_planned
    progress.updated_at = now

    pending = max(0, progress.total_tasks_planned - progress.total_tasks_assigned)
    msg = f'Successfully assigned {actual} tasks!'
    if pending > 0:
        msg += f' {pending} pending — eligible users registering later can receive them automatically.'
    return {'ok': True, 'message': msg, 'assigned': actual, 'pending': pending}


@app.route("/admin/allocate_tasks/<int:campaign_id>", methods=["POST"])
@admin_required
def allocate_tasks(campaign_id):
    try:
        total_tasks_needed = int(request.form.get('total_tasks', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid task count.'}), 400

    if total_tasks_needed <= 0:
        return jsonify({'status': 'error', 'message': 'Task count must be greater than zero.'}), 400

    try:
        now = utc_now()
        campaign = db.session.query(Campaign).with_for_update().filter_by(id=campaign_id).first()
        if not campaign:
            return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404
        if campaign.status != 'Active':
            return jsonify({'status': 'error', 'message': 'Only active campaigns can receive new allocations.'}), 400

        result = perform_task_allocation(campaign, total_tasks_needed, now)
        if not result['ok']:
            db.session.rollback()
            status_code = 404 if 'eligible' in result['message'].lower() else 400
            return jsonify({'status': 'error', 'message': result['message']}), status_code

        db.session.commit()
        return jsonify({
            'status': 'success', 'message': f"✅ {result['message']}",
            'assigned': result['assigned'], 'pending': result['pending']
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.exception(f"Allocation Error for Campaign {campaign_id}: {e}")
        return jsonify({'status': 'error', 'message': 'System error'}), 500


@app.route('/admin/support')
@admin_required
def admin_support():
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    return render_template('admin_support.html', tickets=tickets,
                           open_tickets=len([t for t in tickets if t.status == 'Open']),
                           in_progress=len([t for t in tickets if t.status == 'In Progress']),
                           closed_tickets=len([t for t in tickets if t.status == 'Closed']))


@app.route('/admin/support/ticket/<int:ticket_id>')
@admin_required
def admin_view_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket:
        flash('❌ Ticket not found!', 'error')
        return redirect(url_for('admin_support'))

    if ticket.status == 'Open':
        ticket.status = 'In Progress'
        db.session.commit()
    return render_template('admin_support_ticket.html', ticket=ticket)


@app.route('/admin/support/ticket/<int:ticket_id>/reply', methods=['POST'])
@admin_required
def admin_reply_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket:
        return jsonify({'status': 'error', 'message': 'Ticket not found'}), 404

    message = request.form.get('message', '').strip()
    if len(message) < 5 or len(message) > 5000:
        return jsonify({'status': 'error', 'message': 'Message must be between 5 and 5000 characters'}), 400

    reply = SupportReply(
        ticket_id=ticket_id,
        user_id=None,
        message=message,
        is_admin_reply=True
    )
    db.session.add(reply)
    ticket.updated_at = utc_now()
    notify_user(
        ticket.user.id, f'💬 Admin replied to your support ticket: {ticket.subject}',
        notification_type='support', related_id=ticket.id
    )
    db.session.commit()

    reply_data = {
        'id': reply.id, 'message': reply.message,
        'created_at': reply.created_at.strftime('%d-%m-%Y %H:%M'),
        'user_name': 'Admin', 'is_admin': True, 'ticket_id': ticket_id
    }
    socketio.emit('ticket_reply', reply_data, room=f'ticket_{ticket_id}')

    return jsonify({
        'status': 'success', 'message': 'Reply sent successfully',
        'reply': reply_data
    })


@app.route('/admin/support/ticket/<int:ticket_id>/status/<status>', methods=['POST'])
@admin_required
def update_ticket_status(ticket_id, status):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket or status not in ['Open', 'In Progress', 'Closed']:
        return jsonify({'status': 'error', 'message': 'Invalid request'}), 400

    ticket.status = status
    if status == 'Closed':
        ticket.closed_at = utc_now()
    notify_user(
        ticket.user_id, f'Your support ticket "{ticket.subject}" is now {status}',
        notification_type='support', related_id=ticket.id
    )
    db.session.commit()
    socketio.emit('ticket_status_changed', {'ticket_id': ticket.id, 'status': status}, room=f'ticket_{ticket.id}')

    return jsonify({'status': 'success', 'message': f'Ticket status updated to {status}'})


# ----------------- Static endpoints -----------------

@app.route('/offline.html')
def offline():
    return render_template('offline.html')

@app.route('/static/manifest.json')
def serve_manifest():
    return send_file('static/manifest.json', mimetype='application/manifest+json')

@app.route('/api/sync-pending-tasks', methods=['POST'])
@login_required
def sync_pending_tasks():
    pending = (TaskSubmission.query.join(Task, Task.id == TaskSubmission.task_id)
               .filter(Task.user_id == session['user_id'], TaskSubmission.verification_status == 'Pending').count())
    return jsonify({'status': 'success', 'synced_tasks': pending, 'message': 'Tasks synced successfully'})

@app.route('/uploads/<filename>')
@admin_required
def serve_uploads(filename):
    return send_from_directory(os.path.join(os.getcwd(), app.config.get('UPLOAD_FOLDER', 'uploads')), filename)


# ----------------- Allocation Dashboard -----------------

@app.route("/admin/campaign_allocation_dashboard")
@admin_required
def campaign_allocation_dashboard():
    campaigns_progress = CampaignAllocationProgress.query.all()
    dashboard_data = []

    for progress in campaigns_progress:
        campaign = db.session.get(Campaign, progress.campaign_id)
        if not campaign:
            continue

        planned_tasks = progress.total_tasks_planned or progress.total_tasks_created or 0
        assigned_tasks = progress.total_tasks_assigned or 0
        completed_tasks = progress.total_tasks_completed or 0
        assigned_pct = (assigned_tasks / planned_tasks * 100) if planned_tasks > 0 else 0
        completed_pct = (completed_tasks / planned_tasks * 100) if planned_tasks > 0 else 0
        days_remaining = (progress.campaign_deadline - utc_now()).days if getattr(progress, 'campaign_deadline', None) else 0

        if progress.is_fully_allocated and completed_tasks >= planned_tasks:
            status, status_color = '✅ Campaign Completed', 'success'
        elif assigned_tasks >= planned_tasks:
            status, status_color = '✅ All Tasks Assigned', 'success'
        else:
            status, status_color = f'⏳ {max(0, planned_tasks - assigned_tasks)} tasks waiting', 'warning'

        dashboard_data.append({
            'campaign_id': campaign.id, 'campaign_name': campaign.name,
            'total_tasks': planned_tasks, 'assigned': assigned_tasks,
            'completed': completed_tasks, 'pending': max(0, planned_tasks - assigned_tasks),
            'assigned_pct': round(assigned_pct, 2), 'completed_pct': round(completed_pct, 2),
            'users_assigned': progress.users_count_assigned, 'users_needed': getattr(progress, 'users_count_planned', 0),
            'deadline': progress.campaign_deadline.strftime('%d-%m-%Y') if getattr(progress, 'campaign_deadline', None) else '',
            'days_remaining': days_remaining,
            'status': status, 'status_color': status_color
        })
    return render_template('admin_allocation_dashboard.html', campaigns=dashboard_data)


# ----------------- FORGOT PASSWORD (OTP BASED) -----------------

@app.route("/forgot_password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=['POST'])
def forgot_password():
    if request.method == "GET":
        return render_template('forgot_password.html')

    email = (request.form.get('email') or '').strip().lower()
    if not email:
        flash('❌ Please enter email!', 'error')
        return render_template('forgot_password.html')

    # Do not reveal whether an account exists.
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('✅ If your email exists, an OTP has been sent!', 'success')
        return render_template('forgot_password.html')

    try:
        PasswordResetToken.query.filter_by(user_id=user.id, is_used=False).update({'is_used': True})
        otp = str(secrets.randbelow(900000) + 100000)
        token = PasswordResetToken(
            user_id=user.id,
            email=email,
            reset_token=otp,
            expires_at=utc_now() + timedelta(minutes=15)
        )
        db.session.add(token)
        db.session.flush()
        db.session.commit()

        html_body = f"""
        <div style=\"font-family: Arial, sans-serif; padding: 20px; text-align: center;\">
            <h2>Password Reset OTP</h2>
            <p>Your 6-digit OTP to reset your GMB Earn password is:</p>
            <h1 style=\"color: #0d6efd; letter-spacing: 5px;\">{otp}</h1>
            <p>This OTP is valid for 15 minutes. Do not share it with anyone.</p>
        </div>
        """
        mail.send(Message(
            subject='🔐 Your Password Reset OTP - GMB Earn',
            recipients=[email],
            html=html_body
        ))

        session.pop('otp_verified', None)
        session['reset_token_id'] = token.id
        flash('✅ OTP sent successfully to your email!', 'success')
        return redirect(url_for('verify_otp'))
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Password reset email error: {e}")
        flash('❌ Error occurred while sending email! Check SMTP settings.', 'error')
        return render_template('forgot_password.html')


@app.route("/verify_otp", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=['POST'])
def verify_otp():
    token_id = session.get('reset_token_id')
    if not token_id:
        flash('❌ Invalid request, please start again.', 'error')
        return redirect(url_for('forgot_password'))

    token_record = db.session.get(PasswordResetToken, token_id)
    if not token_record or token_record.is_used or not token_record.expires_at or token_record.expires_at < utc_now():
        session.pop('reset_token_id', None)
        session.pop('otp_verified', None)
        flash('❌ OTP expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == "POST":
        entered_otp = (request.form.get('otp') or '').strip()
        if not secrets.compare_digest(entered_otp, token_record.reset_token):
            flash('❌ Invalid or expired OTP!', 'error')
            return render_template('verify_otp.html', email=token_record.email)

        session['otp_verified'] = True
        flash('✅ OTP Verified! Set your new password.', 'success')
        return redirect(url_for('set_new_password'))

    return render_template('verify_otp.html', email=token_record.email)


@app.route("/set_new_password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=['POST'])
def set_new_password():
    token_id = session.get('reset_token_id')
    if not session.get('otp_verified') or not token_id:
        flash('❌ Session expired. Please verify OTP again.', 'error')
        return redirect(url_for('forgot_password'))

    token_record = db.session.get(PasswordResetToken, token_id)
    if not token_record or token_record.is_used or not token_record.expires_at or token_record.expires_at < utc_now():
        session.clear()
        flash('❌ Reset session expired. Please request a new OTP.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == "POST":
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not new_password or new_password != confirm_password:
            flash('❌ Passwords must match!', 'error')
            return render_template('set_new_password.html')

        valid, msg = is_valid_password(new_password)
        if not valid:
            flash(f'❌ {msg}', 'error')
            return render_template('set_new_password.html')

        try:
            user = db.session.get(User, token_record.user_id)
            if not user:
                session.clear()
                flash('❌ Account not found.', 'error')
                return redirect(url_for('forgot_password'))
            user.set_password(new_password)
            token_record.is_used = True
            db.session.commit()

            try:
                mail.send(Message(
                    subject='✅ Password Changed Successfully',
                    recipients=[user.email],
                    body='Your password has been successfully changed.'
                ))
            except Exception:
                logger.warning('Password changed but confirmation email could not be sent.')

            session.pop('reset_token_id', None)
            session.pop('otp_verified', None)
            flash('✅ Password reset successfully! You can login now.', 'success')
            return redirect(url_for('user_login'))
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Error resetting password: {e}")
            flash('❌ Error resetting password!', 'error')
            return render_template('set_new_password.html')

    return render_template('set_new_password.html')


# ----------------- Analytics & Leaderboard -----------------

@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    approved_tasks = Task.query.filter_by(status='Approved').count()
    rejected_tasks = Task.query.filter_by(status='Rejected').count()
    pending_tasks = TaskSubmission.query.filter_by(verification_status='Pending').count()

    total_earned = db.session.query(db.func.sum(WalletTransaction.points)).filter_by(transaction_type='Earn').scalar() or 0
    total_redeemed = db.session.query(db.func.sum(WalletTransaction.points)).filter_by(transaction_type='Redeem').scalar() or 0
    total_refunded = db.session.query(db.func.sum(WalletTransaction.points)).filter_by(transaction_type='Refund').scalar() or 0

    today = utc_now().date()
    dates = []
    user_counts = []
    for i in range(6, -1, -1):
        target_date = today - timedelta(days=i)
        count = User.query.filter(db.func.date(User.created_at) == target_date).count()
        dates.append(target_date.strftime('%d %b'))
        user_counts.append(count)

    top_campaigns = CampaignAllocationProgress.query.order_by(CampaignAllocationProgress.total_tasks_completed.desc()).limit(5).all()
    campaign_names = []
    campaign_completions = []

    for progress in top_campaigns:
        if progress.campaign:
            campaign_names.append(progress.campaign.name[:15] + '..')
            campaign_completions.append(progress.total_tasks_completed)

    return render_template("admin_analytics.html",
                         approved_tasks=approved_tasks,
                         rejected_tasks=rejected_tasks,
                         pending_tasks=pending_tasks,
                         total_earned=total_earned,
                         total_redeemed=total_redeemed,
                         total_refunded=total_refunded,
                         dates=dates,
                         user_counts=user_counts,
                         campaign_names=campaign_names,
                         campaign_completions=campaign_completions)


def _compute_leaderboard(settings, limit=None):
    excluded_ids = {e.user_id for e in LeaderboardExclusion.query.all()}
    period_start = settings.period_start or utc_now()
    rows = []

    if settings.ranking_basis == 'points':
        earn_rows = (
            db.session.query(Wallet.user_id, db.func.sum(WalletTransaction.points).label('score'))
            .join(WalletTransaction, WalletTransaction.wallet_id == Wallet.id)
            .filter(WalletTransaction.transaction_type == 'Earn')
            .filter(WalletTransaction.transaction_date >= period_start)
            .group_by(Wallet.user_id)
            .all()
        )
        score_map = {uid: (score or 0) for uid, score in earn_rows}
    else:
        task_rows = (
            db.session.query(Task.user_id, db.func.count(Task.id).label('score'))
            .filter(Task.status == 'Approved')
            .filter(Task.submission_date.isnot(None))
            .filter(Task.submission_date >= period_start)
            .group_by(Task.user_id)
            .all()
        )
        score_map = {uid: (score or 0) for uid, score in task_rows}

    if not score_map:
        return []

    all_time_points_rows = (
        db.session.query(Wallet.user_id, db.func.sum(WalletTransaction.points).label('total'))
        .join(WalletTransaction, WalletTransaction.wallet_id == Wallet.id)
        .filter(WalletTransaction.transaction_type == 'Earn')
        .group_by(Wallet.user_id)
        .all()
    )
    total_points_map = {uid: (total or 0) for uid, total in all_time_points_rows}

    all_time_task_rows = (
        db.session.query(Task.user_id, db.func.count(Task.id).label('total'))
        .filter(Task.status == 'Approved')
        .group_by(Task.user_id)
        .all()
    )
    total_tasks_map = {uid: (total or 0) for uid, total in all_time_task_rows}

    users = User.query.filter(User.id.in_(score_map.keys())).all()
    for user in users:
        if user.id in excluded_ids or getattr(user, 'is_blocked', False):
            continue
        rows.append({
            'user_id': user.id,
            'name': user.name or 'User',
            'score': score_map.get(user.id, 0),
            'total_points': total_points_map.get(user.id, 0),
            'total_tasks_completed': total_tasks_map.get(user.id, 0)
        })

    rows.sort(key=lambda r: r['score'], reverse=True)

    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx

    if limit:
        rows = rows[:limit]

    if settings.mask_names:
        for row in rows:
            n = row['name'].strip()
            if len(n) <= 2:
                row['display_name'] = n[0] + '*' if n else 'User'
            else:
                row['display_name'] = n[0] + '*' * (len(n) - 2) + n[-1]
    else:
        for row in rows:
            row['display_name'] = row['name']

    return rows


@app.route("/leaderboard")
@login_required
def leaderboard():
    settings = LeaderboardSettings.get_settings()

    if not settings.is_active:
        flash('ℹ️ Leaderboard is currently disabled by admin.', 'info')
        return redirect(url_for('user_dashboard'))

    all_rows = _compute_leaderboard(settings)
    rows = all_rows[:settings.top_limit]
    current_user_id = session.get('user_id')
    my_rank_row = next((r for r in all_rows if r['user_id'] == current_user_id), None)

    return render_template(
        "leaderboard.html",
        settings=settings,
        rows=rows,
        my_rank_row=my_rank_row
    )


@app.route("/admin/leaderboard")
@admin_required
def admin_leaderboard():
    settings = LeaderboardSettings.get_settings()
    rows = _compute_leaderboard(settings, limit=settings.top_limit)
    excluded_users = LeaderboardExclusion.query.all()
    return render_template(
        "admin_leaderboard.html",
        settings=settings,
        rows=rows,
        excluded_users=excluded_users
    )


@app.route("/admin/leaderboard/settings", methods=["POST"])
@admin_required
def admin_leaderboard_settings():
    settings = LeaderboardSettings.get_settings()
    data = request.form

    settings.is_active = data.get('is_active') == 'on'
    settings.show_on_dashboard = data.get('show_on_dashboard') == 'on'
    settings.mask_names = data.get('mask_names') == 'on'
    settings.title = (data.get('title') or settings.title).strip()[:150]
    settings.subtitle = (data.get('subtitle') or settings.subtitle).strip()[:300]

    ranking_basis = data.get('ranking_basis')
    if ranking_basis in ('approved_tasks', 'points'):
        settings.ranking_basis = ranking_basis

    try:
        top_limit = int(data.get('top_limit', settings.top_limit))
        settings.top_limit = max(3, min(top_limit, 100))
    except (TypeError, ValueError):
        pass

    settings.prize_1st = (data.get('prize_1st') or '').strip()[:200]
    settings.prize_2nd = (data.get('prize_2nd') or '').strip()[:200]
    settings.prize_3rd = (data.get('prize_3rd') or '').strip()[:200]

    try:
        db.session.commit()
        flash('✅ Leaderboard settings updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating leaderboard settings: {str(e)}")
        flash('❌ Failed to update leaderboard settings.', 'error')

    return redirect(url_for('admin_leaderboard'))


@app.route("/admin/leaderboard/reset", methods=["POST"])
@admin_required
def admin_leaderboard_reset():
    settings = LeaderboardSettings.get_settings()
    try:
        settings.period_start = utc_now()
        settings.last_reset_at = utc_now()
        settings.last_reset_by = session.get('admin_id')
        db.session.commit()
        flash('✅ Leaderboard has been reset! New ranking period started.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resetting leaderboard: {str(e)}")
        flash('❌ Failed to reset leaderboard.', 'error')
    return redirect(url_for('admin_leaderboard'))


@app.route("/admin/leaderboard/exclude/<int:user_id>", methods=["POST"])
@admin_required
def admin_leaderboard_exclude(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    try:
        existing = LeaderboardExclusion.query.filter_by(user_id=user_id).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
            return jsonify({'status': 'success', 'message': f'{user.name} included back in leaderboard.', 'excluded': False})
        else:
            reason = request.form.get('reason', '').strip()[:200]
            excl = LeaderboardExclusion(user_id=user_id, excluded_by=session.get('admin_id'), reason=reason)
            db.session.add(excl)
            db.session.commit()
            return jsonify({'status': 'success', 'message': f'{user.name} excluded from leaderboard.', 'excluded': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling leaderboard exclusion: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Database error occurred.'}), 500


@app.route("/admin/allocations")
@admin_required
def admin_allocations():

    status_priority = db.case(
        (func.lower(func.trim(Campaign.status)) == 'active', 0),
        (func.lower(func.trim(Campaign.status)) == 'push', 1),
        (func.lower(func.trim(Campaign.status)) == 'stopped', 2),
        else_=3
    )

    campaigns = Campaign.query.order_by(
        status_priority,
        Campaign.created_at.desc()
    ).all()

    progress_map = {
        row.campaign_id: row for row in CampaignAllocationProgress.query.all()
    }
    all_assignments = UserCampaignTaskAssignment.query.all()
    assignments_by_campaign = {}
    assignment_user_ids = set()
    for assignment in all_assignments:
        assignments_by_campaign.setdefault(assignment.campaign_id, []).append(assignment)
        assignment_user_ids.add(assignment.user_id)
    users_by_id = {
        user.id: user for user in User.query.filter(User.id.in_(assignment_user_ids)).all()
    } if assignment_user_ids else {}

    allocation_data = []

    for campaign in campaigns:
        progress = progress_map.get(campaign.id)
        assignments = assignments_by_campaign.get(campaign.id, [])

        assigned_users = []

        for assign in assignments:
            user = users_by_id.get(assign.user_id)

            if user:
                assigned_users.append({
                    'name': user.name,
                    'email': user.email,
                    'assigned_date': (
                        assign.assigned_at.strftime("%Y-%m-%d %I:%M %p")
                        if assign.assigned_at
                        else "N/A"
                    ),
                    'status': assign.status
                })

        if progress:
            total_created = (
                progress.total_tasks_planned
                or progress.total_tasks_created
                or 0
            )

            total_assigned = progress.total_tasks_assigned or 0

            remaining = max(
                0,
                total_created - total_assigned
            )
        else:
            total_created = 0
            total_assigned = 0
            remaining = 0

        allocation_data.append({
            'campaign': campaign,
            'total_created': total_created,
            'total_assigned': total_assigned,
            'remaining': remaining,
            'assigned_users': assigned_users
        })

    return render_template(
        'admin_allocations.html',
        allocation_data=allocation_data
    )


# ----------------- PROFILE / EDIT -----------------

@app.route("/profile")
def user_profile():
    if 'user_id' not in session:
        flash("Please login to view your profile.", "error")
        return redirect(url_for('user_login'))

    user = User.query.get(session['user_id'])
    wallet = Wallet.query.filter_by(user_id=user.id).first()

    total_tasks = Task.query.filter_by(user_id=user.id).count()
    completed_tasks = Task.query.filter_by(user_id=user.id, status='Approved').count()
    pending_tasks = Task.query.filter(Task.user_id == user.id, Task.status.in_(['Assigned', 'Rejected', 'Submitted'])).count()

    return render_template(
        'profile.html',
        user=user,
        wallet=wallet,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks
    )


@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    if 'user_id' not in session:
        flash("Please login to access this page.", "error")
        return redirect(url_for('user_login'))

    user = User.query.get(session['user_id'])

    if request.method == "POST":
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if name:
            valid, msg = is_valid_name(name)
            if not valid:
                flash(f"❌ {msg}", "error")
                return redirect(url_for('edit_profile'))
            user.name = name
        if phone:
            valid, msg = is_valid_phone(phone)
            if not valid:
                flash(f"❌ {msg}", "error")
                return redirect(url_for('edit_profile'))
            user.mobile = phone

        if current_password or new_password:
            if not current_password:
                flash("❌ Please enter your current password to set a new one.", "error")
                return redirect(url_for('edit_profile'))

            if not user.check_password(current_password):
                flash("❌ Current password is incorrect!", "error")
                return redirect(url_for('edit_profile'))

            if new_password != confirm_password:
                flash("❌ New passwords do not match!", "error")
                return redirect(url_for('edit_profile'))

            valid, msg = is_valid_password(new_password)
            if not valid:
                flash(f"❌ {msg}", "error")
                return redirect(url_for('edit_profile'))

            user.set_password(new_password)

        db.session.commit()
        session['user_name'] = user.name
        session['password_fingerprint'] = password_fingerprint(user)
        flash("✅ Profile updated successfully!", "success")
        return redirect(url_for('user_profile'))

    return render_template('edit_profile.html', user=user)


# ----------------- BACKGROUND SCHEDULED JOBS -----------------
# Monthly quotas are calculated from assigned_date, so no destructive counter
# reset or allocation_month mutation is needed. The old jobs changed historical
# tasks every month and could corrupt fairness calculations.

# ----------------- ADMIN: user management -----------------

@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    try:
        has_audit_history = any((
            Task.query.filter_by(user_id=user.id).first(),
            RedeemRequest.query.filter_by(user_id=user.id).first(),
            SupportTicket.query.filter_by(user_id=user.id).first(),
            WalletTransaction.query.join(Wallet).filter(Wallet.user_id == user.id).first(),
        ))
        if has_audit_history:
            return jsonify({
                'status': 'error',
                'message': 'User has task, wallet, redeem or support history. Block the account instead of deleting financial/audit records.'
            }), 409
        db.session.delete(user)
        db.session.commit()
        return jsonify({'status': 'success', 'message': f'User {user.name} deleted successfully!'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting user: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to delete user due to a database error.'}), 500


@app.route("/admin/user/<int:user_id>/toggle_block", methods=["POST"])
@admin_required
def toggle_block_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    try:
        user.is_blocked = not user.is_blocked
        db.session.commit()
        action = "Blocked" if user.is_blocked else "Unblocked"
        return jsonify({'status': 'success', 'message': f'User {user.name} has been {action} successfully!'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling block for user: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Database error occurred.'}), 500

# ----------------- CUSTOM ERROR HANDLERS -----------------

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'status': 'error', 'message': 'Resource not found'}), 404
    return '<h1>404</h1><p>The page you requested was not found.</p>', 404

@app.errorhandler(405)
def method_not_allowed(e):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'status': 'error', 'message': 'Method not allowed'}), 405
    return '<h1>405</h1><p>This action is not allowed.</p>', 405

@app.errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    logger.exception('Unhandled application error: %s', e)
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    return '<h1>500</h1><p>Something went wrong. Please try again later.</p>', 500

# ----------------- App entrypoint -----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, use_reloader=False)
