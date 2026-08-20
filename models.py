from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import bcrypt

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100))
    mobile = db.Column(db.String(15))
    total_points = db.Column(db.Integer, default=0)
    redeemed_points = db.Column(db.Integer, default=0)
    is_blocked = db.Column(db.Boolean, default=False)
    # PRIORITY SYSTEM: historical assignment count, not a campaign target.
    total_tasks_assigned = db.Column(db.Integer, default=0)  # Total tasks ever assigned
    priority_score = db.Column(db.Float, default=0.0)  # Lowest = Highest Priority
    last_allocation_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    wallet = db.relationship('Wallet', backref='user', uselist=False, cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='user', cascade='all, delete-orphan')
    redeem_requests = db.relationship('RedeemRequest', backref='user', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', cascade='all, delete-orphan')
    support_tickets = db.relationship('SupportTicket', backref='user', cascade='all, delete-orphan')
    support_replies = db.relationship('SupportReply', backref='user', cascade='all, delete-orphan')
    allocations = db.relationship('UserAllocation', backref='user', cascade='all, delete-orphan')
    def set_password(self, password):
        self.password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password.encode('utf-8'))
    def calculate_priority(self):
        self.priority_score = float(self.total_tasks_assigned or 0)
        return self.priority_score

class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    gmb_link = db.Column(db.String(500))
    total_reviews_required = db.Column(db.Integer, default=0)
    points_per_review = db.Column(db.Integer, default=0)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    duration_months = db.Column(db.Integer, default=1)
    status = db.Column(db.String(50), default='Active')
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    total_users_for_campaign = db.Column(db.Integer, default=0)
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by = db.Column(db.Integer, nullable=True)
    # Rewards are earned for an internal feedback/report task. Any external
    # business/review link is optional reference material and is not a reward
    # condition.
    workflow_type = db.Column(db.String(50), nullable=False, default='InternalFeedback')
    review_texts = db.relationship('CampaignReviewText', backref='campaign', cascade='all, delete-orphan')
    # Hard-delete preserves Task history: tasks retain all data except campaign link.
    tasks = db.relationship('Task', backref='campaign', passive_deletes=True)
    allocations = db.relationship('UserAllocation', backref='campaign', cascade='all, delete-orphan')

class CampaignReviewText(db.Model):
    __tablename__ = 'campaign_review_text'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    review_text = db.Column(db.Text)
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    is_used = db.Column(db.Boolean, nullable=False, default=False, index=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserAllocation(db.Model):
    __tablename__ = 'user_allocations'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    allocated_task_count = db.Column(db.Integer, default=0)
    allocation_month = db.Column(db.Integer, default=1)
    allocation_year = db.Column(db.Integer, nullable=True)
    allocated_at = db.Column(db.DateTime, default=datetime.utcnow)

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    # Campaign is historical metadata. Hard deleting a campaign must not delete a task.
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True, index=True)
    review_text_id = db.Column(
        db.Integer,
        db.ForeignKey('campaign_review_text.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    review_text = db.Column(db.String(1000))
    gmb_link = db.Column(db.String(500))
    points_per_review = db.Column(db.Integer, default=0)
    assigned_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    submission_date = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='Assigned', index=True)
    allocation_month = db.Column(db.Integer, default=1)
    allocation_year = db.Column(db.Integer, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True, index=True)
    expiry_reason = db.Column(db.String(255), nullable=True)
    cancel_reason = db.Column(db.String(255), nullable=True)
    hidden_at = db.Column(db.DateTime, nullable=True, index=True)
    reassigned_from_task_id = db.Column(
        db.Integer,
        db.ForeignKey('tasks.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )
    task_kind = db.Column(db.String(50), nullable=False, default='InternalFeedback')
    submission = db.relationship('TaskSubmission', backref='task', uselist=False, cascade='all, delete-orphan')
    review_prompt = db.relationship('CampaignReviewText', foreign_keys=[review_text_id])
    reassigned_from = db.relationship('Task', remote_side=[id], foreign_keys=[reassigned_from_task_id])
    activity_logs = db.relationship(
        'TaskActivityLog', backref='task', cascade='all, delete-orphan',
        order_by='TaskActivityLog.created_at'
    )

class TaskSubmission(db.Model):
    __tablename__ = 'task_submissions'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), unique=True, nullable=False, index=True)
    screenshot_url = db.Column(db.String(500))
    screenshot_hash = db.Column(db.String(64), index=True)
    review_text_submitted = db.Column(db.Text)
    posted_review_url = db.Column(db.String(1000), nullable=True)
    google_place_id = db.Column(db.String(255), nullable=True)
    posted_date = db.Column(db.Date, nullable=True)
    submitted_date = db.Column(db.DateTime, default=datetime.utcnow)
    verification_status = db.Column(db.String(50), default='Pending')
    admin_notes = db.Column(db.Text)
    verified_date = db.Column(db.DateTime)
    verified_by = db.Column(db.Integer)


class TaskActivityLog(db.Model):
    __tablename__ = 'task_activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    campaign_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    from_status = db.Column(db.String(50), nullable=True)
    to_status = db.Column(db.String(50), nullable=True)
    reason = db.Column(db.String(500), nullable=True)
    actor_type = db.Column(db.String(30), nullable=False, default='system')
    actor_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

class Wallet(db.Model):
    __tablename__ = 'wallet'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    total_points = db.Column(db.Integer, default=0)
    available_points = db.Column(db.Integer, default=0)
    redeemed_points = db.Column(db.Integer, default=0)
    transactions = db.relationship('WalletTransaction', backref='wallet', cascade='all, delete-orphan')

class WalletTransaction(db.Model):
    __tablename__ = 'wallet_transactions'
    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'))
    transaction_type = db.Column(db.String(50))
    points = db.Column(db.Integer)
    reference_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)

class RedeemRequest(db.Model):
    __tablename__ = 'redeem_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    points = db.Column(db.Integer)
    payment_method = db.Column(db.String(50))
    payment_details = db.Column(db.String(200))
    status = db.Column(db.String(50), default='In Process')
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)
    processed_by = db.Column(db.Integer)
    rejection_reason = db.Column(db.String(500))

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))
    related_id = db.Column(db.Integer)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SupportTicket(db.Model):
    __tablename__ = 'support_tickets'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50))
    status = db.Column(db.String(50), default='Open')
    priority = db.Column(db.String(20), default='Normal')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    replies = db.relationship('SupportReply', backref='ticket', cascade='all, delete-orphan')

class SupportReply(db.Model):
    __tablename__ = 'support_replies'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_tickets.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    message = db.Column(db.Text, nullable=False)
    is_admin_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserCampaignTaskAssignment(db.Model):
    __tablename__ = 'user_campaign_task_assignment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id', ondelete='CASCADE'), unique=True, nullable=False)
    status = db.Column(db.String(50), default='Assigned')
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref='campaign_assignments')
    campaign = db.relationship('Campaign', backref='user_assignments')
    task = db.relationship('Task', backref='user_assignment')

class CampaignAllocationProgress(db.Model):
    __tablename__ = 'campaign_allocation_progress'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    # Lifecycle counters are intentionally independent: target != created != assigned != completed.
    total_tasks_planned = db.Column(db.Integer, nullable=False, default=0)
    total_tasks_created = db.Column(db.Integer, nullable=False, default=0)
    total_tasks_assigned = db.Column(db.Integer, nullable=False, default=0)
    total_tasks_completed = db.Column(db.Integer, nullable=False, default=0)
    total_tasks_expired = db.Column(db.Integer, nullable=False, default=0)
    total_tasks_cancelled = db.Column(db.Integer, nullable=False, default=0)
    users_count_planned = db.Column(db.Integer, nullable=False, default=0)
    users_count_assigned = db.Column(db.Integer, nullable=False, default=0)
    users_count_completed = db.Column(db.Integer, nullable=False, default=0)
    campaign_deadline = db.Column(db.DateTime)
    is_fully_allocated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    campaign = db.relationship('Campaign', backref='allocation_progress', uselist=False, passive_deletes=True)

class LeaderboardSettings(db.Model):
    __tablename__ = 'leaderboard_settings'
    id = db.Column(db.Integer, primary_key=True)
    is_active = db.Column(db.Boolean, default=True)
    show_on_dashboard = db.Column(db.Boolean, default=True)
    title = db.Column(db.String(150), default='Top Earners Leaderboard')
    subtitle = db.Column(db.String(300), default='Top performing members based on approved reviews')
    ranking_basis = db.Column(db.String(20), default='approved_tasks')
    top_limit = db.Column(db.Integer, default=20)
    mask_names = db.Column(db.Boolean, default=False)
    prize_1st = db.Column(db.String(200), default='')
    prize_2nd = db.Column(db.String(200), default='')
    prize_3rd = db.Column(db.String(200), default='')
    period_start = db.Column(db.DateTime, default=datetime.utcnow)
    last_reset_by = db.Column(db.Integer)
    last_reset_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    @staticmethod
    def get_settings():
        settings = LeaderboardSettings.query.first()
        if not settings:
            settings = LeaderboardSettings()
            db.session.add(settings)
            db.session.commit()
        return settings

class LeaderboardExclusion(db.Model):
    __tablename__ = 'leaderboard_exclusions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), unique=True)
    excluded_by = db.Column(db.Integer)
    excluded_at = db.Column(db.DateTime, default=datetime.utcnow)
    reason = db.Column(db.String(200))
    user = db.relationship('User', backref='leaderboard_exclusion')

class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    email = db.Column(db.String(120), nullable=False)
    reset_token = db.Column(db.String(255), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    user = db.relationship('User', backref='password_reset_tokens')


class RegistrationOTP(db.Model):
    """Short-lived server-side registration state; no plaintext OTP/password."""
    __tablename__ = 'registration_otps'
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(15), nullable=False)
    otp_hash = db.Column(db.String(64), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    resend_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    consumed_at = db.Column(db.DateTime, nullable=True, index=True)
