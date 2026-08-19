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
    # ✅ PRIORITY SYSTEM FIELDS
    total_tasks_assigned = db.Column(db.Integer, default=0)  # Total tasks ever assigned
    priority_score = db.Column(db.Float, default=0.0)  # Lowest = Highest Priority
    last_allocation_date = db.Column(db.DateTime)  # Last allocation date
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    wallet = db.relationship('Wallet', backref='user', uselist=False, cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='user', cascade='all, delete-orphan')
    redeem_requests = db.relationship('RedeemRequest', backref='user', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', cascade='all, delete-orphan')
    support_tickets = db.relationship('SupportTicket', backref='user', cascade='all, delete-orphan')
    support_replies = db.relationship('SupportReply', backref='user', cascade='all, delete-orphan')
    allocations = db.relationship('UserAllocation', backref='user', cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash password using bcrypt"""
        self.password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    def check_password(self, password):
        """Verify password"""
        return bcrypt.checkpw(password.encode('utf-8'), self.password.encode('utf-8'))
    
    def calculate_priority(self):
        """Calculate priority score - Lowest tasks = Highest Priority"""
        self.priority_score = float(self.total_tasks_assigned)
        return self.priority_score


class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    gmb_link = db.Column(db.String(500))
    total_reviews_required = db.Column(db.Integer, default=0)
    points_per_review = db.Column(db.Integer, default=0)  # 1 Point = 1 Rupee
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    duration_months = db.Column(db.Integer, default=1)
    status = db.Column(db.String(50), default='Active')  # Active, Paused, Stopped
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    total_users_for_campaign = db.Column(db.Integer, default=0)

    # ✅ SOFT DELETE: "Delete Campaign" ab row ko DB se hata nahi deta.
    # Sirf is_deleted=True set hota hai taaki admin ki list se gayab ho jaye,
    # lekin campaign ka poora data (tasks, submissions, allocations, review
    # texts, user history) DB me surakshit rahe.
    is_deleted = db.Column(db.Boolean, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by = db.Column(db.Integer, nullable=True)
    
    review_texts = db.relationship('CampaignReviewText', backref='campaign', cascade='all, delete-orphan')
    # ✅ FIX: Task ab cascade-delete NAHI hoga. Campaign delete hone par bhi
    # user ka task history (Approved/Rejected/Submitted) surakshit rahega.
    tasks = db.relationship('Task', backref='campaign')
    allocations = db.relationship('UserAllocation', backref='campaign', cascade='all, delete-orphan')


class CampaignReviewText(db.Model):
    __tablename__ = 'campaign_review_text'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    review_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserAllocation(db.Model):
    __tablename__ = 'user_allocations'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    allocated_task_count = db.Column(db.Integer, default=0)  # Total tasks for this allocation
    allocation_month = db.Column(db.Integer, default=1)  # ✅ Month 1, 2, 3
    allocated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    
    # ✅ Indexing: Querying ke liye fast
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    
    review_text = db.Column(db.String(1000))
    gmb_link = db.Column(db.String(500))

    # ✅ FIX: Points snapshot — campaign delete hone ke baad bhi task ke
    # points sahi dikhein (pehle task.campaign.points_per_review pe depend
    # tha, jo campaign delete hote hi None ho jata tha -> 0 points bug).
    points_per_review = db.Column(db.Integer, default=0)

    # ✅ Indexing: Monthly count check karne ke liye bahut zaroori
    assigned_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    submission_date = db.Column(db.DateTime)
    
    # Status options: Assigned, Submitted, Approved, Rejected, Cancelled
    # ✅ Cancelled = campaign Stop/Delete hone ki wajah se task ab complete
    # nahi kiya ja sakta. Ye user ki galti se alag hai, isliye priority/
    # fairness counters is se touch nahi hote — sirf record ke liye rakha
    # jata hai taaki user ko pata chale ke kya hua.
    status = db.Column(db.String(50), default='Assigned', index=True) 
    
    # ✅ Month tracking: Dynamic handle karne ke liye
    allocation_month = db.Column(db.Integer, default=1) 

    # ✅ Jab task Cancelled ho (campaign stop/delete ki wajah se), yahan
    # reason note ho jata hai taaki user/admin dono ko wajah pata chale.
    cancel_reason = db.Column(db.String(255), nullable=True)
    
    submission = db.relationship('TaskSubmission', backref='task', uselist=False, cascade='all, delete-orphan')


class TaskSubmission(db.Model):
    __tablename__ = 'task_submissions'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), unique=True, nullable=False, index=True)
    screenshot_url = db.Column(db.String(500))
    screenshot_hash = db.Column(db.String(64), index=True)  # SHA-256 hash to detect duplicate/reused screenshots
    review_text_submitted = db.Column(db.Text)
    submitted_date = db.Column(db.DateTime, default=datetime.utcnow)
    verification_status = db.Column(db.String(50), default='Pending')  # Pending, Approved, Rejected
    admin_notes = db.Column(db.Text)
    verified_date = db.Column(db.DateTime)
    verified_by = db.Column(db.Integer)


class Wallet(db.Model):
    __tablename__ = 'wallet'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    total_points = db.Column(db.Integer, default=0)  # Total points earned
    available_points = db.Column(db.Integer, default=0)  # Points available to redeem
    redeemed_points = db.Column(db.Integer, default=0)  # Points already redeemed
    
    transactions = db.relationship('WalletTransaction', backref='wallet', cascade='all, delete-orphan')


class WalletTransaction(db.Model):
    __tablename__ = 'wallet_transactions'
    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'))
    transaction_type = db.Column(db.String(50))  # 'Earn', 'Redeem'
    points = db.Column(db.Integer)  # 1 Point = ₹1
    reference_id = db.Column(db.String(100), unique=True, nullable=False, index=True)  # Idempotency key
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)


class RedeemRequest(db.Model):
    __tablename__ = 'redeem_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    points = db.Column(db.Integer)  # Min 500 points = ₹500
    payment_method = db.Column(db.String(50))  # UPI, Bank, etc.
    payment_details = db.Column(db.String(200))
    status = db.Column(db.String(50), default='In Process')  # In Process, Completed, Failed
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)
    processed_by = db.Column(db.Integer)
    rejection_reason = db.Column(db.String(500))  # Reject krte waqt admin ka reason


# ✅ NOTIFICATION MODEL
class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))  # 'task', 'redeem', 'support', etc.
    related_id = db.Column(db.Integer)  # Task ID, Redeem ID, Ticket ID, etc.
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ✅ SUPPORT TICKET MODEL
class SupportTicket(db.Model):
    __tablename__ = 'support_tickets'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50))  # 'task_help', 'payment', 'account', 'other'
    status = db.Column(db.String(50), default='Open')  # 'Open', 'In Progress', 'Closed'
    priority = db.Column(db.String(20), default='Normal')  # 'Low', 'Normal', 'High', 'Urgent'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    
    replies = db.relationship('SupportReply', backref='ticket', cascade='all, delete-orphan')


# ✅ SUPPORT REPLY MODEL
class SupportReply(db.Model):
    __tablename__ = 'support_replies'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_tickets.id', ondelete='CASCADE'))
    
    # 👇 Yahan nullable=True add kiya gaya hai 👇
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  
    
    message = db.Column(db.Text, nullable=False)
    is_admin_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserCampaignTaskAssignment(db.Model):
    __tablename__ = 'user_campaign_task_assignment'
    id = db.Column(db.Integer, primary_key=True)
    
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    # ✅ FIX: nullable rakha gaya taki campaign delete hone par history wale
    # (Submitted/Approved/Rejected) assignments delete na ho, sirf unlink ho.
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id', ondelete='CASCADE'), unique=True, nullable=False)
    
    # ✅ Status tracking
    status = db.Column(db.String(50), default='Assigned')  # Assigned, Submitted, Approved, Rejected
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    user = db.relationship('User', backref='campaign_assignments')
    campaign = db.relationship('Campaign', backref='user_assignments')
    task = db.relationship('Task', backref='user_assignment')



class CampaignAllocationProgress(db.Model):
    __tablename__ = 'campaign_allocation_progress'
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id', ondelete='CASCADE'))
    
    total_tasks_planned = db.Column(db.Integer)  
    total_tasks_created = db.Column(db.Integer, default=0)
    total_tasks_assigned = db.Column(db.Integer, default=0)
    total_tasks_completed = db.Column(db.Integer, default=0) 
    
    users_count_planned = db.Column(db.Integer)
    users_count_assigned = db.Column(db.Integer, default=0) 
    users_count_completed = db.Column(db.Integer, default=0) 
    
    campaign_deadline = db.Column(db.DateTime)
    is_fully_allocated = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    campaign = db.relationship('Campaign', backref='allocation_progress', uselist=False)

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
