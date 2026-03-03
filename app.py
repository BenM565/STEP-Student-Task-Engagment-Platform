"""
================================================================================
STEP STUDENT TASK ENGAGEMENT PLATFORM - MAIN APPLICATION
================================================================================

Main Flask application file containing:
- Configuration and initialization
- Database models
- Authentication routes
- Task management routes
- Student routes
- Company routes
- Admin routes
- University routes
- Payment and email integration (Iteration 5)
- Real-time events (SSE)

Versions:
- Iteration 1-4: Core platform functionality
- Iteration 5: Portfolio, Search, Email, Payments
- Latest: Fully structured and refactored

References:
- Flask: https://flask.palletsprojects.com/
- Flask-Login: https://flask-login.readthedocs.io/
- SQLAlchemy: https://flask-sqlalchemy.palletsprojects.com/
- Werkzeug: https://werkzeug.palletsprojects.com/
"""
import notifications
from werkzeug.utils import secure_filename

# ============================================================================
# SECTION 1: IMPORTS
# ============================================================================


from email_service import (
    send_application_accepted_notification,
    send_work_submitted_notification,
    send_work_approved_notification,
    send_change_requested_notification,
    send_task_posted_notification,
    send_application_rejected_notification
)
from email_service import send_email
import os
import json
import time
import queue
from datetime import datetime, timedelta
from typing import Dict, List
from functools import wraps


from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import TemplateNotFound

# Flask core imports
from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort,
    Response, stream_with_context, session, jsonify
)

# Flask extensions
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_mail import Mail
from sqlalchemy.dialects import mysql
from sqlalchemy import func
from sqlalchemy.dialects import mysql
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import re

import re

# ONLY these domains are allowed - whitelist approach
VALID_EMAIL_DOMAINS = {
    # Free email providers
    'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'ymail.com',
    'aol.com', 'mail.com', 'protonmail.com', 'tutanota.com',
    'yahoo.ie', 'yahoo.co.uk', 'outlook.ie', 'gmail.co.uk',

    # Ireland ISPs
    'eircom.net', 'indigo.ie', 'tinet.ie', 'esat.net', 'ireland.com',
    'btinternet.com', 'vodafone.ie', 'tiscali.ie',

    # Irish Universities
    'ucd.ie', 'tcd.ie', 'nuig.ie', 'ul.ie', 'dit.ie', 'dcu.ie',
    'atu.ie', 'itsligo.ie', 'itcarlow.ie', 'ittralee.ie', 'itcork.ie',
    'itlimerick.ie', 'tudublin.ie', 'itgalway.ie',

    # UK Universities
    'ox.ac.uk', 'cam.ac.uk', 'ac.uk',

    # Add your work/company domains here
    # 'yourcompany.ie',
    # 'yourcompany.com',
}


def is_valid_email_domain(email):
    """
    Validate that email uses REAL domain (whitelist approach)

    ONLY allows domains in VALID_EMAIL_DOMAINS or recognized institution domains
    Blocks everything else including test/fake domains

    INPUT: email string
    OUTPUT: True if real domain, False otherwise
    """
    if '@' not in email:
        return False

    domain = email.split('@')[1].lower()

    # BLOCK ALL fake/test domains first
    fake_domains = {
        'test.com', 'example.com', 'example.org', 'example.net',
        'test.de', 'localhost', '127.0.0.1', '0.0.0.0', 'fake.com',
        'testmail.com', 'test.mail', 'temp.com', 'temp-mail.org',
        'maildrop.cc', 'throwaway.email', 'guerrillamail.com',
        '10minutemail.com', 'mailinator.com', 'sharklasers.com',
        'yopmail.com', 'tempmail.com', 'cool.com', 'test.ie',
        'email.com', 'mail.com', 'mail.org', 'domain.com',
        'test123.com', 'asdf.com', 'zxcv.com', 'qwerty.com',
        'abc.com', '123.com', 'test-email.com', 'fake-email.com',
        'noreal.com', 'notreal.com', 'spam.com', 'demo.com',
        'test-account.com', 'dummy.com', 'fakemail.com', 'zzz.com',
    }

    # If in fake list, REJECT immediately
    if domain in fake_domains:
        return False

    # Check against whitelist - ONLY allow if in list
    if domain in VALID_EMAIL_DOMAINS:
        return True

    # For .ac.uk (universities), be more permissive
    if domain.endswith('.ac.uk') or domain.endswith('.edu') or domain.endswith('.edu.au'):
        return True

    # Everything else is REJECTED
    return False


# ========== ALTERNATIVE: STRICTER VERSION ==========
# If you want ONLY whitelisted domains (most secure):

def is_valid_email_domain_strict(email):
    """
    STRICT whitelist - only allows known good domains
    No wildcards, no exceptions
    """
    if '@' not in email:
        return False

    domain = email.split('@')[1].lower()

    # ONLY these domains allowed
    ALLOWED_DOMAINS = {
        # Free providers
        'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'ymail.com',
        'aol.com', 'protonmail.com', 'tutanota.com',
        # Ireland
        'yahoo.ie', 'outlook.ie', 'eircom.net', 'indigo.ie', 'esat.net',
        'ucd.ie', 'tcd.ie', 'nuig.ie', 'ul.ie', 'dit.ie', 'dcu.ie',
        'atu.ie', 'tudublin.ie',
        # UK
        'outlook.co.uk', 'yahoo.co.uk',
    }

    return domain in ALLOWED_DOMAINS
# Flask-Login for authentication
try:
    from flask_login import (
        LoginManager, UserMixin, login_user, logout_user,
        login_required, current_user,
    )
    _FLASK_LOGIN_AVAILABLE = True
except Exception:
    _FLASK_LOGIN_AVAILABLE = False
    from functools import wraps
    from flask import session, redirect, url_for

    class UserMixin:
        @property
        def is_authenticated(self):
            return True

        @property
        def is_anonymous(self):
            return False

        def get_id(self):
            return str(getattr(self, "id", "0"))

    class AnonymousUser:
        is_authenticated = False
        is_anonymous = True
        id = None
        role = None

    _user_loader_callback = None

    class LoginManager:
        def __init__(self, app=None):
            self.login_view = "login"
            if app is not None:
                self.init_app(app)

        def init_app(self, app):
            return None

        def user_loader(self, fn):
            global _user_loader_callback
            _user_loader_callback = fn
            return fn

    def _load_current_user():
        uid = session.get("_user_id")
        if _user_loader_callback and uid is not None:
            try:
                user = _user_loader_callback(uid)
                if user:
                    return user
            except Exception:
                pass
        return AnonymousUser()

    def login_user(user):
        try:
            session["_user_id"] = int(user.id)
        except Exception:
            session["_user_id"] = getattr(user, "id", None)

    def logout_user():
        session.pop("_user_id", None)

    def login_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _load_current_user()
            if not getattr(user, "is_authenticated", False):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper

    current_user = property(lambda self: _load_current_user())

# ============================================================================
# ITERATION 5 IMPORTS: Email Service
# ============================================================================

try:
    from email_service import (
        send_task_posted_notification,
        send_application_received_notification,
        send_application_accepted_notification,
        send_application_rejected_notification,
        send_work_submitted_notification,
        send_work_approved_notification,
        send_change_requested_notification,
        send_dispute_notification
    )
except ImportError:
    # Fallback if email_service.py not found
    def _noop(*args, **kwargs):
        return None
    send_task_posted_notification = _noop
    send_application_received_notification = _noop
    send_application_accepted_notification = _noop
    send_application_rejected_notification = _noop
    send_work_submitted_notification = _noop
    send_work_approved_notification = _noop
    send_change_requested_notification = _noop
    send_dispute_notification = _noop

# ============================================================================
# ITERATION 5 IMPORTS: Payment Service
# ============================================================================

try:
    from payment_service import EscrowManager, init_escrow_manager, PaymentError
except ImportError:
    # Fallback if payment_service.py not found
    class PaymentError(Exception):
        pass
    class EscrowManager:
        pass
    def init_escrow_manager():
        return None

# ============================================================================
# SECTION 2: FLASK APP INITIALIZATION
# ============================================================================

app = Flask(__name__)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///step.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# File upload configuration
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB limit

# Ensure upload directory exists
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ============================================================================
# SECTION 3: ITERATION 5 CONFIGURATION - Email & Payments
# ============================================================================

# Email configuration (Flask-Mail)
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv(
    'MAIL_DEFAULT_SENDER',
    'noreply@step-platform.com'
)

# Application URL (for email links)
app.config['APP_URL'] = os.getenv('APP_URL', 'http://localhost:5000')

# Payment configuration (Stripe)
app.config['STRIPE_PUBLIC_KEY'] = os.getenv('STRIPE_PUBLIC_KEY')
app.config['STRIPE_SECRET_KEY'] = os.getenv('STRIPE_SECRET_KEY')
app.config['STRIPE_WEBHOOK_SECRET'] = os.getenv('STRIPE_WEBHOOK_SECRET')
app.config['PLATFORM_FEE_PERCENT'] = float(os.getenv('PLATFORM_FEE_PERCENT', 10))

# Initialize Flask-Mail
mail = Mail(app)


SELECTED_APPLICATION_STATUSES = ("selected", "in_progress")


def select_student_for_task(application) -> None:
    """
    Extract Function:
    Ensure selecting a student is atomic/consistent:
    - mark the chosen application as selected + in_progress
    - move task to in_progress
    - close out all other applications so they no longer appear as pending/available
    """
    task = Task.query.get(application.task_id)
    if task is None:
        abort(404)

    # Mark task as no longer open
    task.status = "in_progress"

    # Mark chosen application
    application.selected = True
    application.status = "selected"

    # Remove availability for other applicants
    other_apps = (
        Application.query.filter_by(task_id=task.id)
        .filter(Application.id != application.id)
        .all()
    )
    for other in other_apps:
        other.selected = False
        if other.status not in ("rejected", "withdrawn", "completed"):
            other.status = "rejected"

    db.session.commit()

# Initialize payment system
escrow_manager = None
if app.config['STRIPE_SECRET_KEY']:
    try:
        escrow_manager = init_escrow_manager()
    except Exception as e:
        print(f"[Warning] Payment system initialization failed: {e}")

# ============================================================================
# SECTION 4: DATABASE INITIALIZATION
# ============================================================================

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ============================================================================
# SECTION 5: REAL-TIME EVENTS (SSE) - Server-Sent Events Broker
# ============================================================================

class EventBroker:
    """
    In-memory pub/sub broker for Server-Sent Events (SSE).

    Publishes real-time notifications to connected clients.
    Single-process only; for production use Redis or similar.
    """

    def __init__(self):
        """Initialize empty subscriber dictionary"""
        self._subscribers: Dict[int, List[queue.Queue]] = {}

    def subscribe(self, user_id: int) -> queue.Queue:
        """Subscribe a user to event stream"""
        q = queue.Queue()
        self._subscribers.setdefault(int(user_id), []).append(q)
        return q

    def unsubscribe(self, user_id: int, q: queue.Queue):
        """Unsubscribe a user from event stream"""
        lst = self._subscribers.get(int(user_id), [])
        if q in lst:
            lst.remove(q)
        if not lst and int(user_id) in self._subscribers:
            del self._subscribers[int(user_id)]

    def publish(self, user_id: int, event: dict):
        """Publish event to all subscribers of a user"""
        for q in list(self._subscribers.get(int(user_id), [])):
            try:
                q.put_nowait(event)
            except Exception:
                pass

# Global event broker instance
broker = EventBroker()

# SSE endpoint for real-time notifications
@app.route("/events")
@login_required
def sse_events():
    """
    Server-Sent Events endpoint for real-time notifications.

    Streams JSON events to connected clients until disconnect.
    Emits keep-alive comments to prevent idle timeout.
    """
    def event_stream(user_id: int):
        """Generator yielding SSE frames"""
        q = broker.subscribe(user_id)
        try:
            yield "retry: 10000\n\n"
            while True:
                try:
                    ev = q.get(timeout=25)
                    payload = json.dumps(ev)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            broker.unsubscribe(user_id, q)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(event_stream(current_user.id)), headers=headers)

# ============================================================================
# SECTION 6: DATABASE MODELS
# ============================================================================

# ============================================================================
# 6.1 USER MODEL - Represents students, companies, admins, university staff
# ============================================================================

class User(UserMixin, db.Model):
    """
    User model representing all user types in the system.

    Attributes:
    - id: Primary key
    - name: Full name
    - email: Email address (unique)
    - password: Hashed password
    - role: User role (student, company, admin, university)
    - verified: Verification status (for students)
    - university_id: Foreign key to university
    - skills: Comma-separated list of skills (students)
    - grades: Academic grades/credentials (students)
    - projects: Project descriptions (students)
    - department: Department name (university staff)
    - trust_score: Dynamic trust score (0-100)
    - stripe_customer_id: Stripe customer ID (companies)
    - profile_url: Public portfolio URL slug (students)
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    verified = db.Column(db.Boolean, default=False)

    # University linkage
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"), nullable=True)

    # Student-specific fields
    skills = db.Column(db.Text)
    grades = db.Column(db.Text)
    projects = db.Column(db.Text)

    # University staff field
    department = db.Column(db.String(120))

    # Trust score (0-100)
    trust_score = db.Column(db.Float, default=0.0)

    # Iteration 5 fields
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    profile_url = db.Column(db.String(120), unique=True, nullable=True)

    # Relationships
    university = db.relationship("University", backref="students", foreign_keys=[university_id])

    def set_password(self, password):
        """Hash and store password"""
        self.password = generate_password_hash(password)

    def check_password(self, password):
        """Verify password against hash"""
        return check_password_hash(self.password, password)

    @property
    def is_company(self) -> bool:
        """Check if user is a company"""
        return (self.role or "").lower() == "company"

    @property
    def is_student(self) -> bool:
        """Check if user is a student"""
        return (self.role or "").lower() == "student"

    @property
    def is_admin(self) -> bool:
        """Check if user is an admin"""
        return (self.role or "").lower() == "admin"

# ============================================================================
# 6.2 UNIVERSITY MODEL
# ============================================================================

class University(db.Model):
    """
    University model for academic institution management.

    Attributes:
    - id: Primary key
    - name: University name
    - domain: Email domain for automatic linking
    - created_at: Creation timestamp
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================================
# 6.3 TASK MODEL - Work posted by companies
# ============================================================================

class Task(db.Model):
    """
    Task model representing work posted by companies.

    Attributes:
    - id: Primary key
    - title: Task title
    - description: Full task description
    - company_id: Foreign key to company user
    - media_file: Uploaded task media
    - created_at: Creation timestamp
    - requirements: Task requirements
    - estimated_hours: Estimated duration
    - tags: Skill tags (comma-separated)
    - payment_type: 'fixed' or 'hourly'
    - fixed_price: Fixed price amount
    - hourly_rate: Hourly rate
    - status: Task status (open, in_progress, completed)
    """

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    media_file = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    requirements = db.Column(db.Text, nullable=True)
    estimated_hours = db.Column(db.Integer, nullable=True)
    tags = db.Column(db.Text)

    # Payment fields
    payment_type = db.Column(db.String(20), nullable=False, default="fixed")
    fixed_price = db.Column(db.Float, nullable=True)
    hourly_rate = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default="open")

    # Relationships
    company = db.relationship("User", backref="tasks", foreign_keys=[company_id])

# ============================================================================
# 6.4 APPLICATION MODEL - Student applications for tasks
# ============================================================================

class Application(db.Model):
    """
    Application model linking students to tasks.

    Attributes:
    - id: Primary key
    - task_id: Foreign key to task
    - student_id: Foreign key to student
    - status: Application status (pending, in_progress, completed, rejected)
    - created_at: Application timestamp
    - media_file: Company-uploaded file
    - review_status: Approval status
    - review_feedback: Company feedback
    - student_file: Student submission file
    - selected: Selection flag
    - submitted_at: Submission timestamp
    - completed_at: Completion timestamp
    - deadline_at: Expected deadline
    - change_requests_count: Number of change requests
    - first_pass_success: Approved without revisions flag
    - payment_intent_id: Stripe PaymentIntent ID (Iteration 5)
    - payment_status: Payment status (pending, authorized, captured, refunded)
    - payment_authorized_at: Authorization timestamp
    - payment_captured_at: Capture timestamp
    """

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    media_file = db.Column(db.String(255))
    review_status = db.Column(db.String(50))
    review_feedback = db.Column(db.Text)
    student_file = db.Column(db.String(255))
    selected = db.Column(db.Boolean, default=False)
    submitted_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    deadline_at = db.Column(db.DateTime, nullable=True)
    change_requests_count = db.Column(db.Integer, default=0)
    first_pass_success = db.Column(db.Boolean, default=False)

    # Iteration 5: Payment fields
    payment_intent_id = db.Column(db.String(255), nullable=True)
    payment_status = db.Column(db.String(20), default="pending")
    payment_authorized_at = db.Column(db.DateTime, nullable=True)
    payment_captured_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("task_id", "student_id", name="uq_application_task_student"),
    )

    # Relationships
    task = db.relationship("Task", backref="applications")
    student = db.relationship("User", backref="applications", foreign_keys=[student_id])

# ============================================================================
# 6.5 LECTURER REFERENCE MODEL
# ============================================================================

class LecturerReference(db.Model):
    """
    Lecturer reference model for student credibility.

    Attributes:
    - id: Primary key
    - student_id: Foreign key to student
    - lecturer_name: Name of lecturer providing reference
    """

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    lecturer_name = db.Column(db.String(150), nullable=False)
    student = db.relationship("User", backref="lecturer_references")

# ============================================================================
# 6.6 PROJECT MEDIA MODEL - Student portfolio files
# ============================================================================

class ProjectMedia(db.Model):
    """
    Project media model for student portfolio.

    Attributes:
    - id: Primary key
    - user_id: Foreign key to student
    - filename: Stored filename
    - title: Display title
    - uploaded_at: Upload timestamp
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(200))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="project_media")

# ============================================================================
# 6.7 NOTIFICATION MODEL
# ============================================================================

class Notification(db.Model):
    """
    Notification model for user alerts.

    Attributes:
    - id: Primary key
    - message: Notification message
    - is_read: Read status flag
    - created_at: Creation timestamp
    - user_id: Foreign key to recipient
    - task_id: Optional related task
    """

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=True)

# ============================================================================
# 6.8 DISPUTE MODEL
# ============================================================================

class Dispute(db.Model):
    """
    Dispute model for conflict resolution.

    Attributes:
    - id: Primary key
    - raised_by_user_id: User who raised the dispute
    - against_user_id: User dispute is against
    - task_id: Related task
    - message: Dispute description
    - status: Status (open, in_review, resolved)
    - resolution_note: Admin resolution note
    - severity: Severity level (1-5)
    - ai_suggested_resolution: AI triage suggestion
    - created_at: Creation timestamp
    """

    id = db.Column(db.Integer, primary_key=True)
    raised_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    against_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open")
    resolution_note = db.Column(db.Text)
    severity = db.Column(db.Integer, default=1)
    ai_suggested_resolution = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    raised_by_user = db.relationship("User", foreign_keys=[raised_by_user_id])
    against_user = db.relationship("User", foreign_keys=[against_user_id])
    task = db.relationship("Task")

# ============================================================================
# 6.9 REVIEW MODEL
# ============================================================================

class Review(db.Model):
    """
    Review model for task completion ratings.

    Attributes:
    - id: Primary key
    - application_id: Related application
    - rater_user_id: User giving review
    - ratee_user_id: User being reviewed
    - rating: Rating (1-5)
    - comment: Review comment
    - created_at: Creation timestamp
    - updated_at: Last update timestamp
    - editable_until: Deadline for edits
    - is_hidden: Hidden status
    """

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    rater_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ratee_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    editable_until = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.utcnow() + timedelta(days=7)
    )
    is_hidden = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint(
            "application_id",
            "rater_user_id",
            name="uq_review_once_per_reviewer_per_application"
        ),
    )

    application = db.relationship("Application")
    rater = db.relationship("User", foreign_keys=[rater_user_id])
    ratee = db.relationship("User", foreign_keys=[ratee_user_id])

# ============================================================================
# 6.10 PAYMENT TRANSACTION MODEL (Iteration 5)
# ============================================================================

class PaymentTransaction(db.Model):
    """
    Payment transaction log for audit trail.

    Tracks all payment events from creation through capture or refund.

    Attributes:
    - id: Primary key
    - application_id: Related application
    - payment_intent_id: Stripe PaymentIntent ID
    - status: Payment status at transaction time
    - event_type: Type of event
    - amount_cents: Amount in cents
    - details: Additional event details
    - created_at: Transaction timestamp
    """

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    payment_intent_id = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    event_type = db.Column(db.String(100), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    application = db.relationship("Application", backref="payment_transactions")

on_time_delivery = db.Column(db.Boolean, default=None)
first_pass_success = db.Column(db.Boolean, default=None)
num_revisions = db.Column(db.Integer, default=0)
submitted_at = db.Column(db.DateTime)
completed_at = db.Column(db.DateTime)
deadline_at = db.Column(db.DateTime)
# ============================================================================
# SECTION 7: FLASK-LOGIN USER LOADER
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    """Load user session by ID"""
    return db.session.get(User, int(user_id))

# ============================================================================
# SECTION 8: AUTHENTICATION ROUTES
# ============================================================================

# ============================================================================
# 8.1 Root Route
# ============================================================================

@app.route("/")
def index():
    """
    Root route - show landing page for unauthenticated users,
    redirect to role-based dashboard for authenticated users.

    Routes to:
    - Unauthenticated → landing page
    - Students → student_dashboard
    - Companies → company_dashboard
    - Admin → admin_home
    - University → university_dashboard
    """
    # If user is authenticated, redirect to their dashboard
    if current_user.is_authenticated:
        if current_user.role == "student":
            return redirect(url_for("student_dashboard"))
        elif current_user.role == "company":
            return redirect(url_for("company_dashboard"))
        elif current_user.role == "admin":
            return redirect(url_for("admin_home"))
        elif current_user.role == "university":
            return redirect(url_for("university_dashboard"))
        else:
            return redirect(url_for("login"))

    # Show landing page for unauthenticated users
    return render_template("landing.html")

# ============================================================================
# 8.2 Registration Route
# ============================================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "student"

        # Basic validation
        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return render_template("register.html")

        # Validate email format
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash("Please enter a valid email address format.", "danger")
            return render_template("register.html")

        # NEW: Validate email domain is REAL (not test/fake)
        if not is_valid_email_domain(email):
            flash(
                "Please use a real email address (Gmail, Outlook, Yahoo, etc). "
                "Test emails and fake domains are not allowed.",
                "danger"
            )
            return render_template("register.html")

        # Check if user already exists
        if User.query.filter_by(email=email).first():
            flash("This email is already registered.", "danger")
            return render_template("register.html")

        try:
            user = User(name=name, email=email, role=role)
            user.set_password(password)

            # Add role-specific fields
            if role == 'company':
                user.company_size = request.form.get('company_size')
                user.website = request.form.get('website')
                user.location = request.form.get('location')
                user.bio = request.form.get('bio')

            elif role == 'student':
                user.skills = request.form.get('skills')
                user.grades = request.form.get('grades')

            db.session.add(user)
            db.session.commit()

            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))

        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
            return render_template("register.html")

    return render_template("register.html")

# ============================================================================
# 8.3 Login Route
# ============================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """User login route"""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        login_user(user)
        flash(f"Welcome, {user.name}!", "success")

        # Redirect based on role
        if user.role == "student":
            return redirect(url_for("student_dashboard"))
        elif user.role == "company":
            return redirect(url_for("company_dashboard"))
        elif user.role == "admin":
            return redirect(url_for("admin_home"))
        else:
            return redirect(url_for("index"))

    return render_template("login.html")
    """
    User login route.
    ...
    """
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        # Enforce organization email policy
        if user.role == "company":
            if "@" in email:
                domain = email.split("@")[1]
                if domain in ["gmail.com", "yahoo.com", "outlook.com"]:
                    msg = "Companies must use a business email address."
                    flash(msg, "warning")
                    return render_template("login.html")

        login_user(user)
        flash(f"Welcome, {user.name}!", "success")
        return redirect(url_for("index"))

    return render_template("login.html")

# ============================================================================
# 8.4 Logout Route
# ============================================================================

@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    """Logout user and redirect to login"""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# ============================================================================
# SECTION 9: STUDENT ROUTES
# ============================================================================

# ============================================================================
# 9.1 Student Dashboard
# ============================================================================

# REPLACE YOUR student_dashboard() ROUTE WITH THIS (around line 915 in app.py)
@app.route("/task/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    """Delete a task (company only) - cascade delete all related data"""
    if current_user.role != "company":
        abort(403)

    task = db.session.get(Task, task_id)
    if not task or task.company_id != current_user.id:
        abort(403)

    print(f"\n{'=' * 70}")
    print(f"DELETING TASK ID {task_id}: {task.title}")
    print(f"{'=' * 70}")

    try:
        # Step 1: Get all applications for this task
        apps_to_delete = Application.query.filter_by(task_id=task_id).all()
        print(f"Found {len(apps_to_delete)} applications to delete...")

        # Step 2: Delete related data for each application
        for app in apps_to_delete:
            print(f"  - Deleting Application ID {app.id}")

            # Delete reviews linked to this application
            reviews = Review.query.filter_by(application_id=app.id).all()
            for review in reviews:
                print(f"    - Deleting Review ID {review.id}")
                db.session.delete(review)

            # Delete notifications linked to this application
            notifs = Notification.query.filter_by(task_id=task_id, user_id=app.student_id).all()
            for notif in notifs:
                print(f"    - Deleting Notification ID {notif.id}")
                db.session.delete(notif)

            # Delete the application
            db.session.delete(app)

        # Step 3: Delete the task
        print(f"Deleting Task ID {task_id}...")
        db.session.delete(task)

        # Step 4: Commit all changes
        db.session.commit()

        print(f"✅ Successfully deleted task and all related data")
        print(f"{'=' * 70}\n")

        flash("Task deleted successfully!", "success")
        return redirect(url_for("company_dashboard"))

    except Exception as e:
        db.session.rollback()
        print(f"❌ ERROR deleting task: {e}")
        print(f"{'=' * 70}\n")
        flash(f"Error deleting task: {str(e)}", "error")
        return redirect(url_for("company_dashboard"))
# REPLACE the student_dashboard() function (around line 1012) with this FIXED version

@app.route("/student")
@login_required
def student_dashboard():
    """Student dashboard with active tasks, pending review, and completed tasks"""
    if current_user.role != "student":
        abort(403)

    # Get all applications for this student
    all_applications = Application.query.filter_by(student_id=current_user.id).all()

    # ACTIVE TASKS: status in ["in_progress"] (selected by company but not yet approved)
    active_apps = [
        a for a in all_applications
        if a.status in ["in_progress"]
    ]

    # PENDING REVIEW: review_status == "pending" (student uploaded, waiting for company review)
    pending_review_apps = [a for a in all_applications if a.review_status == "pending"]

    # PAST TASKS: status == "completed" AND review_status == "approved"
    past_apps = [
        a for a in all_applications
        if a.status == "completed" and a.review_status == "approved"
    ]

    # Get ratings for this student
    reviews = Review.query.filter_by(ratee_user_id=current_user.id).all()
    rating_count = len(reviews)
    rating_avg = sum(r.rating for r in reviews) / rating_count if rating_count > 0 else 0

    # Get notifications
    notifications = Notification.query.filter_by(user_id=current_user.id).all()
    unread_count = len([n for n in notifications if not n.is_read])

    return render_template(
        "student_dashboard.html",
        active_apps=active_apps,
        pending_review_apps=pending_review_apps,
        past_apps=past_apps,
        applications=all_applications,
        reviews=reviews,
        rating_count=rating_count,
        rating_avg=rating_avg,
        notifications=notifications,
        unread_count=unread_count
    )

# ============================================================================
# 9.2 Browse Tasks
# ============================================================================

@app.route("/browse-tasks")
def browse_tasks():
    """Browse available tasks - ONLY show unselected tasks to students"""
    # Get all open tasks (keeping existing behavior: Task.query.all()).
    all_tasks = Task.query.all()

    tasks = []
    for task in all_tasks:
        # Detect selection robustly:
        # - either selected flag is True
        # - or status indicates selection/in progress
        selected_app = (
            Application.query.filter_by(task_id=task.id)
            .filter(
                (Application.selected.is_(True)) |
                (Application.status.in_(SELECTED_APPLICATION_STATUSES))
            )
            .first()
        )

        # If NO student selected yet, show to everyone
        if not selected_app:
            tasks.append(task)
        # If student IS selected, only show to that student
        elif current_user.is_authenticated and current_user.role == "student":
            if selected_app.student_id == current_user.id:
                tasks.append(task)

    # Handle search filters
    q = request.args.get("q", "").strip()
    hours_max = request.args.get("hours_max")

    if q:
        tasks = [t for t in tasks if q.lower() in t.title.lower() or q.lower() in (t.description or "").lower()]

    if hours_max:
        try:
            hours_max = int(hours_max)
            tasks = [t for t in tasks if t.estimated_hours and t.estimated_hours <= hours_max]
        except:
            pass

    return render_template("browse_tasks.html", tasks=tasks, q=q, hours_max=hours_max)


# ============================================================================
# 9.3 Apply to Task
# ============================================================================

@app.route("/task/<int:task_id>")
def task_detail(task_id):
    """View task details and submission area"""
    # Get the task
    task = db.session.get(Task, task_id)
    if not task:
        abort(404)

    # Get all applications for this task (for company view)
    applications_for_company = Application.query.filter_by(task_id=task_id).all()

    # If student is logged in, get THEIR application
    application = None
    if current_user.is_authenticated and current_user.role == "student":
        application = Application.query.filter_by(
            student_id=current_user.id,
            task_id=task_id
        ).first()

    return render_template(
        "task_detail.html",
        task=task,
        application=application,
        applications_for_company=applications_for_company
    )

# ============================================================================
# 9.4 Student Profile
# ============================================================================

@app.route('/profile')
@login_required
def profile():
    """Display user profile - company or student specific"""
    user = current_user

    if user.role == 'company':
        return render_template('company_profile.html', user=user)
    else:
        # For students and others, use existing template
        return render_template('profile.html', user=user)

# ============================================================================
# 9.5 Student Notifications
# ============================================================================

@app.route("/student/notifications")
@login_required
def student_notifications():
    """Show student's notifications"""
    if current_user.role != "student":
        abort(403)

    notifications = Notification.query.filter_by(
        user_id=current_user.id
    ).order_by(Notification.created_at.desc()).all()

    return render_template("student_notifications.html", notifications=notifications)

# ============================================================================
# 9.6 Mark Notification Read
# ============================================================================

@app.route("/student/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notif_id):
    """Mark notification as read"""
    notification = db.session.get(Notification, notif_id)
    if not notification or notification.user_id != current_user.id:
        abort(403)

    notification.is_read = True
    db.session.commit()
    flash("Notification marked as read.", "success")
    return redirect(request.referrer or url_for("student_notifications"))

# ============================================================================
# 9.7 Upload Project Media
# ============================================================================

@app.route("/student/upload-project-media", methods=["POST"])
@login_required
def student_upload_project_media():
    """Student uploads submission - moves to pending review"""
    if current_user.role != "student":
        abort(403)

    # Get application_id
    application_id = request.args.get("application_id") or request.form.get("application_id")

    if not application_id:
        flash("No application specified.", "error")
        return redirect(request.referrer or url_for("student_dashboard"))

    try:
        application_id = int(application_id)
    except:
        flash("Invalid application ID.", "error")
        return redirect(request.referrer or url_for("student_dashboard"))

    application = db.session.get(Application, application_id)
    if not application or application.student_id != current_user.id:
        abort(403)

    # Get file
    if "file" not in request.files:
        flash("No file provided.", "error")
        return redirect(request.referrer or url_for("student_dashboard"))

    file = request.files["file"]
    if file.filename == "":
        flash("No file selected.", "error")
        return redirect(request.referrer or url_for("student_dashboard"))

    # Save file
    from werkzeug.utils import secure_filename
    filename = f"app_{application.id}_{int(datetime.now().timestamp())}_{secure_filename(file.filename)}"
    os.makedirs("uploads", exist_ok=True)
    filepath = os.path.join("uploads", filename)
    file.save(filepath)

    # UPDATE APPLICATION - SET REVIEW_STATUS
    application.student_file = filename
    application.submitted_at = datetime.now()
    application.review_status = "pending"  # ✅ CRITICAL

    db.session.commit()

    # SEND EMAIL TO COMPANY
    try:
        send_email(
            recipient_email=application.task.company.email,
            subject=f"Work Submitted: {current_user.name} uploaded work for {application.task.title}",
            html_body=f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">
                        <h2 style="color: #0066cc;">Work Submitted!</h2>
                        <p>Student: <strong>{current_user.name}</strong></p>
                        <p>Task: <strong>{application.task.title}</strong></p>
                        <p>Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                        <p><a href="http://localhost:5000/company/applicants/{application.task.id}" style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Review Submission</a></p>
                        <p>Best regards,<br>STEP Platform Team</p>
                    </div>
                </body>
            </html>
            """,
            text_body=f"Work Submitted!\n\nStudent: {current_user.name}\nTask: {application.task.title}\nSubmitted: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\nReview at: http://localhost:5000/company/applicants/{application.task.id}"
        )
        print(f"✅ EMAIL SENT to {application.task.company.email} - Work submitted")
    except Exception as e:
        print(f"❌ EMAIL FAILED: {e}")

    flash("Work submitted successfully!", "success")
    return redirect(url_for("task_detail", task_id=application.task_id))
# ============================================================================
# 9.8 Add Lecturer Reference
# ============================================================================

# ============================================================================
# PUBLIC STUDENT PORTFOLIO
# ============================================================================

@app.route("/portfolio/<string:slug>")
def public_portfolio(slug):
    # Get student by slug
    student = User.query.filter_by(
        profile_url=slug,
        role="student"
    ).first_or_404()

    # Get completed applications
    completed_apps = Application.query.filter_by(
        student_id=student.id,
        status="completed"
    ).all()

    # Extract tasks from completed applications
    completed_tasks = []
    for app in completed_apps:
        task = Task.query.get(app.task_id)
        if task:
            completed_tasks.append(task)

    completed_tasks_count = len(completed_tasks)

    # Get reviews (only if you have Review model)
    try:
        reviews = Review.query.filter_by(
            reviewee_id=student.id
        ).all()
    except:
        reviews = []

    # Calculate average rating safely
    average_rating = 0
    if reviews:
        average_rating = sum(r.rating for r in reviews) / len(reviews)
    elif student.trust_score:
        average_rating = student.trust_score / 20

    return render_template(
        "portfolio.html",
        student=student,
        completed_tasks_count=completed_tasks_count,
        completed_tasks=completed_tasks,
        reviews=reviews,
        average_rating=average_rating
    )

@app.route("/dispute/new")
@login_required
def dispute_new():
    return "<h3>Dispute page coming soon</h3>"

@app.route("/company/application/<int:app_id>/accept", methods=["POST"])
@login_required
def accept_application(app_id):
    """Accept application - select student and FORCE task status update"""
    print(f"\n{'=' * 70}")
    print(f"🎯 ACCEPT_APPLICATION CALLED!")
    print(f"{'=' * 70}")

    if current_user.role != "company":
        abort(403)

    application = db.session.get(Application, app_id)
    if not application or application.task.company_id != current_user.id:
        abort(403)

    print(f"BEFORE UPDATE:")
    print(f"  application.status: {application.status}")
    print(f"  application.task.status: {application.task.status}")
    print(f"  application.selected: {application.selected}")

    # ============================================================================
    # UPDATE APPLICATION
    # ============================================================================
    application.selected = True
    application.status = "in_progress"

    # ============================================================================
    # UPDATE TASK STATUS - THIS IS THE CRITICAL PART
    # ============================================================================
    print(f"\nUPDATING task status...")
    task = application.task
    print(f"  Before: task.status = '{task.status}'")
    task.status = "in_progress"
    print(f"  After: task.status = '{task.status}'")

    # ============================================================================
    # REJECT ALL OTHER APPLICANTS
    # ============================================================================
    other_applications = Application.query.filter_by(task_id=application.task_id).all()
    print(f"\nRejecing {len(other_applications) - 1} other applicants...")
    for other_app in other_applications:
        if other_app.id != application.id:
            print(f"  Rejecting application ID {other_app.id}")
            other_app.status = "rejected"

    # ============================================================================
    # COMMIT CHANGES
    # ============================================================================
    print(f"\nCOMMITTING to database...")
    db.session.commit()

    print(f"\nAFTER COMMIT (checking database):")
    fresh_app = db.session.get(Application, app_id)
    fresh_task = db.session.get(Task, application.task_id)
    print(f"  fresh_app.status: {fresh_app.status}")
    print(f"  fresh_task.status: {fresh_task.status}")
    print(f"  fresh_app.selected: {fresh_app.selected}")

    print(f"{'=' * 70}\n")

    # ============================================================================
    # SEND EMAIL TO SELECTED STUDENT
    # ============================================================================
    try:
        send_email(
            recipient_email=application.student.email,
            subject=f"You were selected for {application.task.title}!",
            html_body=f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">
                        <h2 style="color: #00aa00;">You Were Selected! 🎉</h2>
                        <p>Hi {application.student.name},</p>
                        <p>Great news! You have been selected for <strong>{application.task.title}</strong></p>
                        <p>Company: <strong>{application.task.company.name}</strong></p>
                        <p><a href="http://localhost:5000/task/{application.task.id}" style="background-color: #00aa00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">View Task & Upload Work</a></p>
                        <p>Best regards,<br>STEP Platform Team</p>
                    </div>
                </body>
            </html>
            """,
            text_body=f"You were selected for {application.task.title}!\n\nCompany: {application.task.company.name}\n\nView at: http://localhost:5000/task/{application.task.id}"
        )
        print(f"✅ EMAIL SENT to {application.student.email}")
    except Exception as e:
        print(f"❌ EMAIL FAILED: {e}")

    # ============================================================================
    # CREATE NOTIFICATION
    # ============================================================================
    notif = Notification(
        user_id=application.student_id,
        task_id=application.task_id,
        message=f"You were selected for: {application.task.title}"
    )
    db.session.add(notif)
    db.session.commit()

    flash("Student selected!", "success")
    return redirect(request.referrer or url_for("company_view_applicants", task_id=application.task_id))

@app.route("/student/add-lecturer-reference", methods=["POST"])
@login_required
def add_lecturer_reference():
    """Add lecturer reference to profile"""
    if current_user.role != "student":
        abort(403)

    lecturer_name = request.form.get("lecturer_name", "").strip()

    if not lecturer_name:
        flash("Lecturer name is required.", "danger")
        return redirect(url_for("profile"))

    reference = LecturerReference(
        student_id=current_user.id,
        lecturer_name=lecturer_name
    )
    db.session.add(reference)
    db.session.commit()

    flash("Lecturer reference added!", "success")
    return redirect(url_for("profile"))


@app.route('/company/application/<int:application_id>/review', methods=['POST'])
@login_required
def review_submission(application_id):
    app_obj = Application.query.get_or_404(application_id)

    if app_obj.task.company_id != current_user.id:
        abort(403)

    review_status = request.form.get('review_status')
    review_feedback = request.form.get('review_feedback', '')
    rating = request.form.get('rating', type=int)

    if review_status not in ['approved', 'changes_requested']:
        flash('Invalid review status.', 'danger')
        return redirect(request.referrer or url_for('company_dashboard'))

    try:
        app_obj.review_status = review_status
        app_obj.review_feedback = review_feedback

        if review_status == 'approved':
            app_obj.completed_at = datetime.utcnow()
            app_obj.status = 'completed'
            app_obj.first_pass_success = True

            # Mark task as completed (moves to past tasks)
            task = Task.query.get(app_obj.task_id)
            task.status = 'completed'

            if rating and 1 <= rating <= 5:
                review = Review(
                    application_id=application_id,
                    rater_user_id=current_user.id,
                    ratee_user_id=app_obj.student_id,
                    rating=rating,
                    comment=review_feedback
                )
                db.session.add(review)

            message = f'Your submission for "{app_obj.task.title}" was approved!'
        else:
            app_obj.change_requests_count = int(app_obj.change_requests_count or 0) + 1
            message = f'Changes requested for "{app_obj.task.title}"'

        db.session.commit()

        notification = Notification(
            user_id=app_obj.student_id,
            message=message,
            task_id=app_obj.task_id
        )
        db.session.add(notification)
        db.session.commit()

        flash('Review submitted successfully.', 'success')
        return redirect(request.referrer or url_for('task_detail', task_id=app_obj.task_id))

    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
        return redirect(request.referrer or url_for('company_dashboard'))


# ============================================================================
# FIX 2: UPDATE student_performance_breakdown to calculate weighted score
# REPLACE student_performance_breakdown function (around line 1727) with this:

# REPLACE your student_performance_breakdown function with this DEBUG VERSION

@app.route("/student/performance-breakdown")
@login_required
def student_performance_breakdown():
    """
    Student performance breakdown showing weighted performance metrics.

    FIXED ISSUES:
    1. Now calculates weighted score including on-time delivery and first-pass acceptance
    2. Students can now view their own performance breakdown
    """

    # ✅ FIX: Verify current user is a student
    if current_user.role != "student":
        abort(403)

    # Initialize metrics dictionary with default values
    metrics = {
        "tasks_completed": 0,
        "total_applications": 0,
        "on_time_count": 0,
        "on_time_pct": 0,
        "first_pass_count": 0,
        "first_pass_pct": 0,
        "avg_rating": 0,
        "review_count": 0,
        "quality_score": 0,
        "first_pass_score": 0,
        "on_time_score": 0,
        "completion_score": 0,
        "weighted_score": 0,
        "grade": "N/A",
        "change_requests": 0
    }

    try:
        # ✅ FIX: Query all approved, completed applications
        completed_apps = Application.query.filter(
            Application.student_id == current_user.id,
            Application.status == "completed",
            Application.review_status == "approved"
        ).all()

        # If no completed tasks, return default metrics
        if not completed_apps:
            return render_template(
                "student_performance_breakdown.html",
                metrics=metrics,
                completed=[]
            )

        # ✅ FIX: Calculate basic counts
        tasks_completed = len(completed_apps)
        metrics["tasks_completed"] = tasks_completed

        # ✅ FIX: Calculate On-Time Delivery
        on_time_count = 0
        for app in completed_apps:
            if app.deadline_at and app.completed_at:
                if app.completed_at <= app.deadline_at:
                    on_time_count += 1
            elif app.deadline_at is None:
                on_time_count += 1

        metrics["on_time_count"] = on_time_count
        metrics["on_time_pct"] = int((on_time_count / tasks_completed * 100)) if tasks_completed > 0 else 0

        # ✅ FIX: Calculate First-Pass Acceptance
        first_pass_count = sum(
            1 for app in completed_apps
            if app.first_pass_success == True or app.change_requests_count == 0
        )
        metrics["first_pass_count"] = first_pass_count
        metrics["first_pass_pct"] = int((first_pass_count / tasks_completed * 100)) if tasks_completed > 0 else 0

        # ✅ FIX: Calculate Average Rating
        reviews = Review.query.filter(
            Review.ratee_user_id == current_user.id,
            Review.is_hidden == False
        ).all()

        if reviews:
            metrics["review_count"] = len(reviews)
            metrics["avg_rating"] = round(sum(r.rating for r in reviews) / len(reviews), 2)
        else:
            metrics["review_count"] = 0
            metrics["avg_rating"] = 0

        # ✅ FIX: Calculate revision requests
        metrics["change_requests"] = sum(app.change_requests_count for app in completed_apps)

        # ✅ FIX: WEIGHTED SCORE CALCULATION
        # Component Scores (all 0-100)
        if metrics["avg_rating"] > 0:
            metrics["quality_score"] = min(100, (metrics["avg_rating"] / 5.0) * 100)
        else:
            metrics["quality_score"] = 0

        metrics["completion_score"] = min(100, (tasks_completed / 10.0) * 100)
        metrics["on_time_score"] = metrics["on_time_pct"]
        metrics["first_pass_score"] = metrics["first_pass_pct"]

        # Apply weights: Quality 30%, Completion 25%, On-Time 25%, First-Pass 20%
        metrics["weighted_score"] = round(
            (metrics["quality_score"] * 0.30) +
            (metrics["completion_score"] * 0.25) +
            (metrics["on_time_score"] * 0.25) +
            (metrics["first_pass_score"] * 0.20),
            1
        )

        # ✅ FIX: Assign Grade
        score = metrics["weighted_score"]
        if score >= 90:
            metrics["grade"] = "A"
        elif score >= 80:
            metrics["grade"] = "B"
        elif score >= 70:
            metrics["grade"] = "C"
        elif score >= 60:
            metrics["grade"] = "D"
        else:
            metrics["grade"] = "F"

        # ✅ FIX: Render template with calculated metrics
        return render_template(
            "student_performance_breakdown.html",
            metrics=metrics,
            completed=completed_apps
        )

    except Exception as e:
        print(f"Error in student_performance_breakdown: {str(e)}")
        import traceback
        traceback.print_exc()
        flash("Error loading performance breakdown", "error")
        return redirect(url_for("student_dashboard"))



# ============================================================================
# SECTION 10: ITERATION 5 ROUTES - Portfolio, Search, Payments
# ============================================================================

# ============================================================================
# 10.1 Student Public Portfolio
# ============================================================================

@app.route("/portfolio/<int:student_id>")
def portfolio(student_id):
    """
    PUBLIC student portfolio page.

    Access: Anyone (no login required)
    URL: /portfolio/<student_id>

    Shows:
    - Student profile with verification badge
    - Trust score, completed tasks count, average rating
    - Skills as visual tags
    - Portfolio media files
    - Lecturer references
    - Reviews from companies
    """
    # Get student
    student = db.session.get(User, student_id)

    # Verify student exists and is student role
    if not student or student.role != "student":
        abort(404)

    # Get completed applications (approved work)
    completed_applications = Application.query.filter(
        Application.student_id == student_id,
        Application.status == "completed",
        Application.review_status == "approved"
    ).all()

    # Get completed tasks
    completed_tasks = [app_obj.task for app_obj in completed_applications]
    completed_tasks_count = len(completed_applications)

    # Calculate average rating
    reviews = Review.query.filter(
        Review.ratee_user_id == student_id,
        Review.is_hidden == False
    ).all()

    average_rating = (
        sum(r.rating for r in reviews) / len(reviews) if reviews else 0.0
    )

    # Render portfolio template
    return render_template(
        "portfolio.html",
        student=student,
        completed_tasks=completed_tasks,
        completed_tasks_count=completed_tasks_count,
        average_rating=average_rating,
        reviews=reviews
    )

# ADD THIS ROUTE TO app.py (around line 1300, after review_submission)

@app.route("/application/<int:application_id>/review", methods=["POST"])
@login_required
def create_or_update_review(application_id):
    """Create or update review/rating for student or company"""
    application = db.session.get(Application, application_id)
    if not application:
        abort(404)

    # Check if current user is company or student
    is_company = current_user.role == "company" and application.task.company_id == current_user.id
    is_student = current_user.role == "student" and application.student_id == current_user.id

    if not (is_company or is_student):
        abort(403)

    # Get form data
    rating = request.form.get("rating")
    comment = request.form.get("comment", "").strip()

    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            flash("Rating must be between 1 and 5", "error")
            return redirect(request.referrer)
    except:
        flash("Invalid rating", "error")
        return redirect(request.referrer)

    print(f"\n{'='*70}")
    print(f"CREATE_OR_UPDATE_REVIEW CALLED")
    print(f"  Application ID: {application_id}")
    print(f"  Reviewer: {current_user.name} (role: {current_user.role})")
    print(f"  Rating: {rating}/5")
    print(f"  Comment: {comment[:50]}..." if comment else "  Comment: (none)")
    print(f"{'='*70}\n")

    # Determine who is rating whom
    if is_company:
        # Company rating student
        ratee_id = application.student_id
        rater_id = current_user.id
    else:
        # Student rating company
        ratee_id = application.task.company_id
        rater_id = current_user.id

    # Check if review already exists
    existing_review = Review.query.filter_by(
        application_id=application_id,
        rater_user_id=rater_id,
        ratee_user_id=ratee_id
    ).first()

    if existing_review:
        # Update existing review
        print(f"Updating existing review ID {existing_review.id}")
        existing_review.rating = rating
        existing_review.comment = comment if comment else None
    else:
        # Create new review
        print(f"Creating new review")
        existing_review = Review(
            application_id=application_id,
            rater_user_id=rater_id,
            ratee_user_id=ratee_id,
            rating=rating,
            comment=comment if comment else None
        )
        db.session.add(existing_review)

    db.session.commit()
    print(f"✅ Review saved successfully\n")

    flash("Review submitted!", "success")
    return redirect(request.referrer or url_for("student_dashboard" if is_student else "company_dashboard"))
# ============================================================================
# 10.2 Company Search & Filter Students
# ============================================================================

@app.route("/company/search-students")
@login_required
def company_search_students():
    """
    Company interface to search and filter students.

    Access: Company users only
    URL: /company/search-students

    Query Parameters:
    - search: Name/email search term
    - skills: Comma-separated skills
    - min_trust_score: Minimum trust score (0-100)
    - verified: 'yes' for verified only
    - sort_by: trust_score, rating, completed_tasks, name

    Features:
    - Text search (case-insensitive)
    - Multi-criteria filtering
    - Sorting options
    - Student preview cards
    - View portfolio button
    - Post task button
    - CSV export
    """
    # Check if user is company
    if not current_user.is_company:
        abort(403)

    # Get filter parameters
    search = request.args.get("search", "").strip()
    skills = request.args.get("skills", "").strip()
    min_trust_score = int(request.args.get("min_trust_score", 0))
    verified_only = request.args.get("verified") == "yes"
    sort_by = request.args.get("sort_by", "trust_score")

    # Build query
    query = User.query.filter_by(role="student")

    # Filter by search term (name or email)
    if search:
        query = query.filter(
            db.or_(
                func.lower(User.name).contains(func.lower(search)),
                func.lower(User.email).contains(func.lower(search))
            )
        )

    # Filter by skills
    if skills:
        skill_list = [s.strip().lower() for s in skills.split(",")]
        query = query.filter(
            db.or_(*[
                func.lower(User.skills).contains(skill)
                for skill in skill_list
            ])
        )

    # Filter by trust score
    if min_trust_score > 0:
        query = query.filter(User.trust_score >= min_trust_score)

    # Filter by verification status
    if verified_only:
        query = query.filter_by(verified=True)

    # Sort results
    if sort_by == "rating":
        query = query.outerjoin(
            Review,
            Review.ratee_user_id == User.id
        ).group_by(User.id).order_by(
            func.avg(Review.rating).desc()
        )
    elif sort_by == "completed_tasks":
        query = query.outerjoin(
            Application,
            (Application.student_id == User.id) &
            (Application.status == "completed")
        ).group_by(User.id).order_by(
            func.count(Application.id).desc()
        )
    elif sort_by == "name":
        query = query.order_by(User.name)
    else:  # trust_score (default)
        query = query.order_by(User.trust_score.desc())

    # Execute query
    students = query.all()

    # Calculate student metrics
    student_ratings = {}
    student_completed_tasks = {}

    for student in students:
        # Calculate average rating
        reviews = Review.query.filter(
            Review.ratee_user_id == student.id,
            Review.is_hidden == False
        ).all()
        student_ratings[student.id] = (
            sum(r.rating for r in reviews) / len(reviews) if reviews else 0
        )

        # Count completed tasks
        completed = Application.query.filter(
            Application.student_id == student.id,
            Application.status == "completed",
            Application.review_status == "approved"
        ).count()
        student_completed_tasks[student.id] = completed

    return render_template(
        "company_search_students.html",
        students=students,
        student_ratings=student_ratings,
        student_completed_tasks=student_completed_tasks
    )



# ============================================================================
# SECTION 11: COMPANY ROUTES
# ============================================================================

# ============================================================================
# 11.1 Company Dashboard
# ============================================================================

@app.route("/company")
@login_required
def company_dashboard():
    if current_user.role != "company":
        abort(403)

    # Get tasks by status
    tasks_awaiting_selection = Task.query.filter(
        Task.company_id == current_user.id,
        Task.status == "open"
    ).all()

    tasks_active = Task.query.filter(
        Task.company_id == current_user.id,
        Task.status == "in_progress"
    ).all()

    tasks_past = Task.query.filter(
        Task.company_id == current_user.id,
        Task.status == "completed"
    ).all()

    # Get notifications (use notif_list to avoid naming conflicts)
    notif_list = (
        Notification.query
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )

    unread_notifs = [n for n in notif_list if not n.is_read]
    read_notifs = [n for n in notif_list if n.is_read]

    return render_template(
        "company_dashboard.html",
        tasks_awaiting_selection=tasks_awaiting_selection,
        tasks_active=tasks_active,
        tasks_past=tasks_past,
        company_unread_count=len(unread_notifs),
        company_unread=unread_notifs,
        company_read=read_notifs
    )




# ============================================================================
# 11.2 Add Task
# ============================================================================
@app.route("/task/<int:task_id>/apply", methods=["POST"])
@login_required
def apply_to_task(task_id):
    """Student applies for a task"""
    if current_user.role != "student":
        abort(403)

    task = db.session.get(Task, task_id)
    if not task:
        abort(404)

    # Check if already applied
    existing = Application.query.filter_by(student_id=current_user.id, task_id=task_id).first()
    if existing:
        flash("You've already applied for this task.", "warning")
        return redirect(url_for("browse_tasks"))

    app_obj = Application(student_id=current_user.id, task_id=task_id, status="pending")
    db.session.add(app_obj)
    db.session.commit()

    # ✅ SEND EMAIL TO COMPANY
    try:
        send_email(
            recipient_email=task.company.email,
            subject=f"New Application: {current_user.name} applied for {task.title}",
            html_body=f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">
                        <h2 style="color: #0066cc;">New Application Received!</h2>
                        <p>Hi {task.company.name},</p>
                        <p>A student has applied for your task:</p>
                        <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                            <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                            <p><strong>Applicant:</strong> {current_user.name}</p>
                            <p><strong>Email:</strong> {current_user.email}</p>
                            <p><strong>Skills:</strong> {current_user.skills or 'Not specified'}</p>
                        </div>
                        <p>
                            <a href="http://localhost:5000/company/applicants/{task.id}" 
                               style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                                Review Application
                            </a>
                        </p>
                        <p>Best regards,<br>STEP Platform Team</p>
                    </div>
                </body>
            </html>
            """,
            text_body=f"New Application: {current_user.name} applied for {task.title}\n\nEmail: {current_user.email}\nSkills: {current_user.skills or 'Not specified'}\n\nReview at: http://localhost:5000/company/applicants/{task.id}"
        )
        print(f"✅ EMAIL SENT to {task.company.email} - New application from {current_user.name}")
    except Exception as e:
        print(f"❌ EMAIL FAILED: {e}")

    flash(f"Applied to '{task.title}'!", "success")
    return redirect(url_for("browse_tasks"))


@app.route("/add-task", methods=["GET", "POST"])
@login_required
def add_task():
    """Create a new task"""
    if current_user.role != "company":
        abort(403)

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        requirements = request.form.get("requirements")
        estimated_hours = request.form.get("estimated_hours")
        fixed_price = request.form.get("fixed_price")

        task = Task(
            company_id=current_user.id,
            title=title,
            description=description,
            requirements=requirements,
            estimated_hours=int(estimated_hours) if estimated_hours else None,
            fixed_price=float(fixed_price) if fixed_price else None,
            status="open"
        )
        db.session.add(task)
        db.session.commit()

        # ✅ SEND EMAIL TO EACH STUDENT
        try:
            all_students = User.query.filter_by(role="student").all()
            for student in all_students:
                send_email(
                    recipient_email=student.email,
                    subject=f"New Task Alert: {task.title}",
                    html_body=f"""
                    <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">
                                <h2 style="color: #0066cc;">New Task Posted!</h2>
                                <p>Hi {student.name},</p>
                                <p>A new task has been posted:</p>
                                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                                    <p><strong>Posted by:</strong> {current_user.name}</p>
                                    <p><strong>Description:</strong></p>
                                    <p>{task.description[:200] if task.description else 'No description'}...</p>
                                    {f'<p><strong>Estimated Hours:</strong> {task.estimated_hours}</p>' if task.estimated_hours else ''}
                                    {f'<p><strong>Fixed Price:</strong> €{task.fixed_price}</p>' if task.fixed_price else ''}
                                </div>
                                <p>
                                    <a href="http://localhost:5000/task/{task.id}" 
                                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                                        View Task & Apply
                                    </a>
                                </p>
                                <p>Best regards,<br>STEP Platform Team</p>
                            </div>
                        </body>
                    </html>
                    """,
                    text_body=f"New Task: {task.title}\n\nPosted by: {current_user.name}\n\nDescription: {task.description[:200] if task.description else 'No description'}\n\nView at: http://localhost:5000/task/{task.id}"
                )
                print(f"✅ EMAIL SENT to {student.email} - New task: {task.title}")
        except Exception as e:
            print(f"❌ EMAIL FAILED: {e}")

        flash("Task posted successfully!", "success")
        return redirect(url_for("company_dashboard"))

    return render_template("add_task.html")

# ============================================================================
# 11.3 Edit Task
# ============================================================================
# ========== UPDATE YOUR edit_profile ROUTE IN app.py ==========
# Replace your current edit_profile route with this:

@app.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Edit profile - role-based"""
    user = current_user

    if request.method == 'POST':
        # Common fields
        user.name = request.form.get('name', user.name)
        user.email = request.form.get('email', user.email)

        # Company fields only
        if user.role == 'company':
            user.bio = request.form.get('bio', user.bio)
            user.company_size = request.form.get('company_size')
            user.website = request.form.get('website')
            user.phone = request.form.get('phone')
            user.location = request.form.get('location')

        # Student fields only
        elif user.role == 'student':
            user.skills = request.form.get('skills')
            user.grades = request.form.get('grades')
            user.projects = request.form.get('projects')
            user.references = request.form.get('references')

        try:
            db.session.commit()
            flash('Profile updated!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')

    # GET - show appropriate template
    if user.role == 'company':
        return render_template('company_edit_profile.html', user=user)
    elif user.role == 'student':
        return render_template('student_edit_profile.html', user=user)
    else:
        return render_template('edit_profile.html', user=user)


# ========== ALSO UPDATE YOUR database model ==========
# Add these fields to your User model if they don't exist:


@app.route("/company/task/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    """Edit existing task"""
    if current_user.role != "company":
        abort(403)

    task = db.session.get(Task, task_id)
    if not task or task.company_id != current_user.id:
        abort(404)

    if request.method == "POST":
        task.title = request.form.get("title", task.title)
        task.description = request.form.get("description", task.description)
        task.requirements = request.form.get("requirements", task.requirements)
        task.tags = request.form.get("tags", task.tags)
        db.session.commit()
        flash("Task updated successfully!", "success")
        return redirect(url_for("company_dashboard"))

    return render_template("edit_task.html", task=task)

# ============================================================================
# 11.4 Company Applicants
# ============================================================================

@app.route("/company/applicants/<int:task_id>")
@login_required
def company_view_applicants(task_id):
    """View applicants for a specific task"""
    if current_user.role != "company":
        abort(403)
    task = db.session.get(Task, task_id)
    if not task or task.company_id != current_user.id:
        abort(404)
    applications = Application.query.filter_by(task_id=task_id).all()
    return render_template("company_applicants.html", task=task, applications=applications)


# ============================================================================
# 11.5 Accept Applicant
# ============================================================================



# ============================================================================
# 11.6 Reject Application
# ============================================================================

@app.route("/company/application/<int:app_id>/reject", methods=["POST"])
@login_required
def reject_application(app_id):
    """Reject application"""
    if current_user.role != "company":
        abort(403)

    application = db.session.get(Application, app_id)
    if not application or application.task.company_id != current_user.id:
        abort(403)

    application.status = "rejected"

    # ✅ SEND EMAIL TO STUDENT
    try:
        send_application_rejected_notification(
            application,
            application.task,
            application.student,
            reason="Application did not meet requirements"
        )
        print(f"✅ EMAIL SENT to {application.student.email} - Application rejected")
    except Exception as e:
        print(f"❌ EMAIL FAILED: {e}")

    db.session.commit()
    flash("Application rejected.", "warning")
    return redirect(request.referrer or url_for("company_applicants"))



# ============================================================================
# 11.7 Approve Submitted Work
# ============================================================================
@app.route("/company/application/<int:app_id>/approve", methods=["POST"])
@login_required
def approve_application(app_id):
    """
    Approve submitted work and capture payment.

    Marks work as completed, captures payment from escrow,
    and sends approval email with payment details to student.
    """
    if current_user.role != "company":
        abort(403)

    application = db.session.get(Application, app_id)
    if not application or application.task.company_id != current_user.id:
        abort(403)

    # Capture payment (Iteration 5)
    try:
        if application.payment_intent_id and application.payment_status != "captured":
            capture_result = escrow_manager.capture_payment(
                payment_intent_id=application.payment_intent_id,
                application_id=application.id
            )

            application.payment_status = "captured"
            application.payment_captured_at = datetime.utcnow()

            # Log payment capture
            log_entry = PaymentTransaction(
                application_id=application.id,
                payment_intent_id=application.payment_intent_id,
                status="captured",
                event_type="payment_captured",
                amount_cents=int(application.task.fixed_price * 100) if application.task.fixed_price else 0,
                details="Payment captured and released to student"
            )
            db.session.add(log_entry)

    except PaymentError as e:
        flash(f"Payment capture failed: {e.message}. Work approved but payment is pending.", "warning")

    # Mark as completed
    application.completed_at = datetime.utcnow()
    application.status = "completed"
    application.review_status = "approved"
    application.selected = False

    task = application.task
    task.status = "completed"

    # Send approval email
    try:
        send_work_approved_notification(
            application,
            application.task,
            application.student
        )
    except Exception as e:
        print(f"[Email] Failed to send approval notification: {e}")

    # Create notification
    notif = Notification(
        user_id=application.student_id,
        task_id=application.task_id,
        message=f"Your work was approved for: {application.task.title} - Payment released!"
    )
    db.session.add(notif)

    # Publish real-time event
    broker.publish(application.student_id, {
        "type": "work_approved",
        "task_title": application.task.title,
        "amount": application.task.fixed_price
    })

    db.session.commit()
    flash("Work approved and payment released!", "success")
    return redirect(request.referrer or url_for("company_applicants"))




@app.route("/admin")
@login_required
def admin_home():
    """
    Admin dashboard showing:
    - Unverified students
    - Open disputes
    - System statistics
    """
    if not getattr(current_user, "is_admin", False):
        abort(403)

    unverified = User.query.filter_by(role="student", verified=False).all()
    open_disputes = Dispute.query.filter(
        Dispute.status != "resolved"
    ).order_by(Dispute.created_at.desc()).all()

    # Statistics
    students_q = User.query.filter_by(role="student")
    total_students = students_q.count()
    verified_students = students_q.filter_by(verified=True).count()
    unverified_students = students_q.filter_by(verified=False).count()

    total_disputes = Dispute.query.count()
    open_disputes_count = Dispute.query.filter(
        Dispute.status != "resolved"
    ).count()

    return render_template(
        "admin_home.html",
        unverified=unverified,
        open_disputes=open_disputes,
        total_students=total_students,
        verified_students=verified_students,
        unverified_students=unverified_students,
        total_disputes=total_disputes,
        open_disputes_count=open_disputes_count
    )

# ============================================================================
# SECTION 13: ERROR HANDLERS
# ============================================================================

@app.route('/company-disputes')
@login_required
def company_disputes():
    """Display all disputes raised against the company"""
    if current_user.role != 'company':
        abort(403)

    disputes = Dispute.query.filter(
        Dispute.against_user_id == current_user.id
    ).order_by(Dispute.created_at.desc()).all()

    return render_template('company_disputes.html', disputes=disputes)


@app.route('/company-disputes/<int:dispute_id>')
@login_required
def company_dispute_detail(dispute_id):
    """View specific dispute detail"""
    if current_user.role != 'company':
        abort(403)

    dispute = Dispute.query.get_or_404(dispute_id)

    if dispute.against_user_id != current_user.id:
        abort(403)

    return render_template('company_dispute_detail.html', dispute=dispute)

@app.route('/disputes')
@login_required
def disputes_view():
    """Route dispatcher for disputes based on user role"""
    user = current_user

    if user.role == 'student':
        return redirect(url_for('student_disputes'))
    elif user.role == 'company':
        return redirect(url_for('company_disputes'))
    elif user.role == 'admin':
        return redirect(url_for('admin_disputes'))
    elif user.role == 'university':
        try:
            return redirect(url_for('university_disputes'))
        except:
            return redirect(url_for('university_dashboard'))
    else:
        abort(403)
@app.route("/student/application/<int:application_id>/accept-selection", methods=["POST"])
@login_required
def accept_selected_task(application_id):
    if current_user.role != "student":
        abort(403)
    app_obj = db.session.get(Application, application_id)
    if not app_obj or app_obj.student_id != current_user.id:
        abort(403)
    app_obj.status = "in_progress"
    db.session.commit()
    flash("Task accepted!", "success")
    return redirect(url_for("student_notifications"))

@app.route("/student/application/<int:application_id>/decline-selection", methods=["POST"])
@login_required
def decline_selected_task(application_id):
    if current_user.role != "student":
        abort(403)
    app_obj = db.session.get(Application, application_id)
    if not app_obj or app_obj.student_id != current_user.id:
        abort(403)
    app_obj.status = "pending"
    db.session.commit()
    flash("Task declined.", "info")
    return redirect(url_for("student_notifications"))

@app.route("/company/students")
@login_required
def company_students():
    """View past students company has worked with"""
    if current_user.role != "company":
        abort(403)

    completed_apps = Application.query.join(Task).filter(
        Task.company_id == current_user.id,
        Application.status == "completed"
    ).all()

    student_data = []
    seen_ids = set()

    for app_obj in completed_apps:
        if app_obj.student_id not in seen_ids:
            seen_ids.add(app_obj.student_id)
            student = db.session.get(User, app_obj.student_id)
            if student:
                reviews = Review.query.filter_by(ratee_user_id=student.id, rater_user_id=current_user.id).all()
                rating_avg = sum(r.rating for r in reviews) / len(reviews) if reviews else 0
                student_data.append({
                    "student": student,
                    "rating_avg": rating_avg,
                    "rating_count": len(reviews)
                })

    return render_template("company_students.html", student_data=student_data)

@app.route("/company/student/<int:student_id>")
@login_required
def company_view_student(student_id):
    """View student profile from company perspective"""
    if current_user.role != "company":
        abort(403)
    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        abort(404)
    reviews = Review.query.filter_by(ratee_user_id=student_id, rater_user_id=current_user.id).all()
    project_media = ProjectMedia.query.filter_by(user_id=student_id).all()
    references = LecturerReference.query.filter_by(student_id=student_id).all()
    rating_avg = sum(r.rating for r in reviews) / len(reviews) if reviews else 0
    return render_template(
        "company_view_student.html",
        student=student,
        reviews=reviews,
        project_media=project_media,
        references=references,
        rating_avg=rating_avg,
        rating_count=len(reviews),
        show_avg=(len(reviews) >= 5)
    )

@app.errorhandler(403)
def forbidden(error):
    """Handle 403 forbidden"""
    try:
        return render_template("403.html"), 403
    except TemplateNotFound:
        return "403 Forbidden", 403


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 server errors"""
    db.session.rollback()
    try:
        return render_template("500.html"), 500
    except TemplateNotFound:
        return "500 Internal Server Error", 500

# ============================================================================
# SECTION 14: MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    """
    Run Flask development server.

    For production, use a WSGI server like Gunicorn:
    $ gunicorn app:app
    """
    # Ensure tables exist BEFORE starting the dev server.
    # Long-term: prefer `flask db upgrade`, but this prevents "table doesn't exist"
    # crashes during local development.
    with app.app_context():
        db.create_all()

    app.run(debug=True, host="0.0.0.0", port=5000)