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

# ============================================================================
# SECTION 1: IMPORTS
# ============================================================================

import os
import json
import time
import queue
from datetime import datetime, timedelta
from typing import Dict, List
from functools import wraps

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
from werkzeug.security import generate_password_hash, check_password_hash

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

# Fallback function for missing email service
def _noop_email(*args, **kwargs):
    """No-op function for when email service is not available"""
    return None

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
except (ImportError, ModuleNotFoundError):
    # Fallback if email_service.py not found
    send_task_posted_notification = _noop_email
    send_application_received_notification = _noop_email
    send_application_accepted_notification = _noop_email
    send_application_rejected_notification = _noop_email
    send_work_submitted_notification = _noop_email
    send_work_approved_notification = _noop_email
    send_change_requested_notification = _noop_email
    send_dispute_notification = _noop_email

# ============================================================================
# ITERATION 5 IMPORTS: Payment Service
# ============================================================================

# Fallback classes for missing payment service
class PaymentError(Exception):
    """Payment processing error"""
    message = "Payment error"

class EscrowManager:
    """Fallback escrow manager when service not available"""
    pass

def init_escrow_manager():
    """Initialize escrow manager (returns None if not available)"""
    return None

try:
    from payment_service import EscrowManager, init_escrow_manager, PaymentError
except (ImportError, ModuleNotFoundError):
    # Fallback definitions above will be used
    pass

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
@login_required
def index():
    """
    Root route - redirect to role-based dashboard.

    Routes to:
    - Students → student_dashboard
    - Companies → company_dashboard
    - Admin → admin_home
    - University → university_dashboard
    """
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

# ============================================================================
# 8.2 Registration Route
# ============================================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    User registration route.

    Supports:
    - Student registration (with university auto-linking)
    - Company registration
    - Email validation
    - Password hashing
    """
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password")
        role = request.form.get("role", "student").lower()

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return render_template("register.html")

        # Check email uniqueness
        existing = User.query.filter(func.lower(User.email) == email).first()
        if existing:
            flash("An account with this email already exists. Please log in.", "warning")
            return redirect(url_for("login"))

        # Create user
        user = User(name=name, email=email, role=role)
        user.set_password(password)

        # Auto-link students to university by email domain
        if role == "student":
            try:
                email_domain = (email.split("@", 1)[1] or "").lower()
                uni = University.query.filter(
                    func.lower(University.domain) == email_domain
                ).first()

                if not uni:
                    # Try subdomain matching
                    for dom in [ed.domain for ed in University.query.all()]:
                        if dom and (email_domain == dom or email_domain.endswith("." + dom)):
                            uni = University.query.filter_by(domain=dom).first()
                            break

                if uni:
                    user.university_id = uni.id
                else:
                    # Create university entry if needed
                    parts = email_domain.split(".")
                    base = ".".join(parts[-2:]) if len(parts) >= 2 else email_domain
                    uni = University(name=base.upper(), domain=email_domain)
                    db.session.add(uni)
                    db.session.flush()
                    user.university_id = uni.id
            except Exception as e:
                print(f"[Register] University linking error: {e}")

        db.session.add(user)
        db.session.commit()
        flash(f"Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

# ============================================================================
# 8.3 Login Route
# ============================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """
    User login route.

    Authenticates user and creates session.
    Enforces organization email policy per role.
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

@app.route("/logout")
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

@app.route("/student")
@login_required
def student_dashboard():
    """
    Student dashboard showing:
    - Active tasks (applications in progress)
    - Completed tasks (approved applications)
    - Pending applications
    - Recent notifications
    - Trust score and ratings
    """
    if current_user.role != "student":
        abort(403)

    # Get student's applications grouped by status
    active_applications = Application.query.filter(
        Application.student_id == current_user.id,
        Application.status.in_(["pending", "in_progress"])
    ).all()

    completed_applications = Application.query.filter(
        Application.student_id == current_user.id,
        Application.status == "completed"
    ).all()

    # Get recent notifications
    notifications = Notification.query.filter(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc()).limit(10).all()

    # Fetch ratings
    ratings = Review.query.filter(
        Review.ratee_user_id == current_user.id,
        Review.is_hidden == False
    ).all()

    average_rating = (
        sum(r.rating for r in ratings) / len(ratings) if ratings else 0.0
    )

    return render_template(
        "student_dashboard.html",
        active_applications=active_applications,
        completed_applications=completed_applications,
        notifications=notifications,
        average_rating=average_rating,
        num_ratings=len(ratings)
    )

# ============================================================================
# 9.2 Browse Tasks
# ============================================================================

@app.route("/browse-tasks")
def browse_tasks():
    """
    Browse available tasks.
    Accessible to authenticated students.
    Shows task details and application option.
    """
    tasks = Task.query.filter_by(status="open").all()
    return render_template("browse_tasks.html", tasks=tasks)

# ============================================================================
# 9.3 Apply to Task
# ============================================================================

@app.route("/task/<int:task_id>/apply", methods=["POST"])
@login_required
def apply_to_task(task_id):
    """
    Submit application for a task.

    POST Parameters:
    - task_id: Task to apply for

    Creates Application record and notifies company.
    """
    if current_user.role != "student":
        abort(403)

    task = db.session.get(Task, task_id)
    if not task:
        abort(404)

    # Check for existing application
    existing = Application.query.filter_by(
        task_id=task_id,
        student_id=current_user.id
    ).first()

    if existing:
        flash("You have already applied for this task.", "warning")
        return redirect(url_for("browse_tasks"))

    # Create application
    application = Application(task_id=task_id, student_id=current_user.id)
    db.session.add(application)
    db.session.commit()

    # Send notification email to company
    try:
        send_application_received_notification(application, task, task.company)
    except Exception as e:
        print(f"[Email] Failed to send application notification: {e}")

    # Create in-app notification
    notif = Notification(
        user_id=task.company_id,
        task_id=task_id,
        message=f"{current_user.name} applied for: {task.title}"
    )
    db.session.add(notif)

    # Publish real-time event
    broker.publish(task.company_id, {
        "type": "application_received",
        "student_name": current_user.name,
        "task_title": task.title,
        "application_id": application.id
    })

    db.session.commit()
    flash("Application submitted successfully!", "success")
    return redirect(url_for("browse_tasks"))

# ============================================================================
# 9.4 Student Profile
# ============================================================================

@app.route("/profile")
@app.route("/profile/<int:user_id>")
@login_required
def profile(user_id=None):
    """
    Student profile view and edit.

    Shows:
    - Basic info (name, email, university)
    - Skills and grades
    - Projects and portfolio media
    - Lecturer references
    """
    if user_id is None:
        user = current_user
    else:
        user = db.session.get(User, user_id)
        if not user or user.role != "student":
            abort(404)

    edit = request.args.get("edit", 0) == "1" and user.id == current_user.id

    if request.method == "POST" and edit and user.id == current_user.id:
        user.name = request.form.get("name", user.name)
        user.email = request.form.get("email", user.email)
        user.skills = request.form.get("skills", user.skills)
        user.grades = request.form.get("grades", user.grades)
        user.projects = request.form.get("projects", user.projects)
        db.session.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile", user_id=user.id))

    return render_template("profile.html", user=user, edit=edit)

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
    """
    Upload project media for portfolio.

    Supports file uploads with optional title.
    """
    if current_user.role != "student":
        abort(403)

    if "file" not in request.files:
        flash("No file provided.", "danger")
        return redirect(url_for("profile"))

    file = request.files["file"]
    title = request.form.get("title", "Project").strip()

    if file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("profile"))

    # Save file
    filename = f"{int(time.time())}_{file.filename}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    # Create media record
    media = ProjectMedia(user_id=current_user.id, filename=filename, title=title)
    db.session.add(media)
    db.session.commit()

    flash("Project media uploaded successfully!", "success")
    return redirect(url_for("profile"))

# ============================================================================
# 9.8 Add Lecturer Reference
# ============================================================================

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
    """
    Company dashboard showing:
    - Tasks awaiting selection
    - Active tasks with applicants
    - Task overview with quick actions
    - New search button for finding students
    """
    if current_user.role != "company":
        abort(403)

    # Get company's tasks by status
    awaiting_selection = Task.query.filter(
        Task.company_id == current_user.id,
        Task.status == "open"
    ).all()

    active = Task.query.filter(
        Task.company_id == current_user.id,
        Task.status == "in_progress"
    ).all()

    # Get recent notifications
    notifications = (
        Notification.query
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "company_dashboard.html",
        awaiting_selection=awaiting_selection,
        active=active,
        notifications=notifications,
        search_students_url=url_for('company_search_students')
    )

# ============================================================================
# 11.2 Add Task
# ============================================================================

@app.route("/company/add-task", methods=["GET", "POST"])
@login_required
def add_task():
    """
    Create new task.

    POST Parameters:
    - title: Task title
    - description: Task description
    - requirements: Task requirements
    - estimated_hours: Estimated duration
    - tags: Skill tags (comma-separated)
    - payment_type: 'fixed' or 'hourly'
    - fixed_price: Fixed price amount
    - hourly_rate: Hourly rate
    """
    if current_user.role != "company":
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        requirements = request.form.get("requirements", "").strip()
        estimated_hours = request.form.get("estimated_hours", "")
        tags = request.form.get("tags", "").strip()
        payment_type = request.form.get("payment_type", "fixed")
        fixed_price = request.form.get("fixed_price", "")
        hourly_rate = request.form.get("hourly_rate", "")

        if not title or not description:
            flash("Title and description are required.", "danger")
            return render_template("add_task.html")

        # Create task
        task = Task(
            title=title,
            description=description,
            requirements=requirements or None,
            estimated_hours=int(estimated_hours) if estimated_hours else None,
            tags=tags,
            company_id=current_user.id,
            payment_type=payment_type,
            fixed_price=float(fixed_price) if fixed_price else None,
            hourly_rate=float(hourly_rate) if hourly_rate else None
        )

        db.session.add(task)
        db.session.commit()

        # Send notifications to matching students
        try:
            if tags:
                task_tags = [tag.strip().lower() for tag in tags.split(",")]
                for student in User.query.filter_by(role="student").all():
                    if student.skills:
                        student_skills = [s.strip().lower() for s in student.skills.split(",")]
                        if any(skill in student_skills for skill in task_tags):
                            send_task_posted_notification(task, current_user)
        except Exception as e:
            print(f"[Email] Failed to send task notifications: {e}")

        flash("Task posted successfully!", "success")
        return redirect(url_for("company_dashboard"))

    return render_template("add_task.html")

# ============================================================================
# 11.3 Edit Task
# ============================================================================

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

@app.route("/company/applicants")
@login_required
def company_applicants():
    """
    View applicants for company's tasks.

    Shows all applications sorted by task.
    Allows reviewing, selecting, rejecting applicants.
    """
    if current_user.role != "company":
        abort(403)

    # Get all applications for company's tasks
    applications = Application.query.join(
        Task,
        Application.task_id == Task.id
    ).filter(Task.company_id == current_user.id).all()

    return render_template("company_applicants.html", applications=applications)

# ============================================================================
# 11.5 Accept Applicant
# ============================================================================

@app.route("/company/application/<int:app_id>/accept", methods=["POST"])
@login_required
def accept_application(app_id):
    """
    Accept application and initialize payment.

    Creates Stripe payment intent and sends acceptance email to student.
    """
    if current_user.role != "company":
        abort(403)

    application = db.session.get(Application, app_id)
    if not application or application.task.company_id != current_user.id:
        abort(403)

    application.selected = True
    application.status = "in_progress"

    # Initialize payment (Iteration 5)
    if not application.payment_intent_id and escrow_manager:
        try:
            amount_cents = int(application.task.fixed_price * 100) if application.task.fixed_price else 0

            payment_intent = escrow_manager.create_payment_intent(
                company_id=application.task.company_id,
                student_id=application.student_id,
                application_id=application.id,
                amount_cents=amount_cents,
                task_id=application.task_id,
                task_title=application.task.title
            )

            application.payment_intent_id = payment_intent['id']
            application.payment_status = "pending"

            # Log payment event
            log_entry = PaymentTransaction(
                application_id=application.id,
                payment_intent_id=payment_intent['id'],
                status="pending",
                event_type="payment_intent_created",
                amount_cents=amount_cents,
                details=f"Payment intent created for {application.task.title}"
            )
            db.session.add(log_entry)

        except PaymentError as e:
            flash(f"Payment setup error: {e.message}", "warning")

    # Send acceptance email
    try:
        send_application_accepted_notification(
            application,
            application.task,
            application.student
        )
    except Exception as e:
        print(f"[Email] Failed to send acceptance notification: {e}")

    # Create notification
    notif = Notification(
        user_id=application.student_id,
        task_id=application.task_id,
        message=f"Congratulations! You were selected for: {application.task.title}"
    )
    db.session.add(notif)

    # Publish real-time event
    broker.publish(application.student_id, {
        "type": "application_accepted",
        "task_title": application.task.title,
        "task_id": application.task_id
    })

    db.session.commit()
    flash("Application accepted!", "success")
    return redirect(request.referrer or url_for("company_applicants"))

# ============================================================================
# 11.6 Reject Application
# ============================================================================

@app.route("/company/application/<int:app_id>/reject", methods=["POST"])
@login_required
def reject_application(app_id):
    """
    Reject application and refund payment if applicable.

    Refunds payment from escrow and sends rejection email to student.
    """
    if current_user.role != "company":
        abort(403)

    application = db.session.get(Application, app_id)
    if not application or application.task.company_id != current_user.id:
        abort(403)

    reason = request.form.get("reason", "")

    # Refund payment if authorized (Iteration 5)
    try:
        if application.payment_intent_id and application.payment_status == "authorized":
            refund_result = escrow_manager.refund_payment(
                payment_intent_id=application.payment_intent_id,
                application_id=application.id,
                reason="requested_by_customer"
            )

            application.payment_status = "refunded"

            # Log refund
            log_entry = PaymentTransaction(
                application_id=application.id,
                payment_intent_id=application.payment_intent_id,
                status="refunded",
                event_type="payment_refunded",
                amount_cents=int(application.task.fixed_price * 100) if application.task.fixed_price else 0,
                details="Application rejected - payment refunded"
            )
            db.session.add(log_entry)

    except PaymentError as e:
        flash(f"Warning: Payment refund failed. Manual intervention may be needed.", "warning")

    # Mark as rejected
    application.status = "rejected"
    application.review_status = "rejected"

    # Send rejection email
    try:
        send_application_rejected_notification(
            application,
            application.task,
            application.student,
            reason=reason
        )
    except Exception as e:
        print(f"[Email] Failed to send rejection notification: {e}")

    # Create notification
    notif = Notification(
        user_id=application.student_id,
        task_id=application.task_id,
        message=f"Application status: {application.task.title}"
    )
    db.session.add(notif)

    db.session.commit()
    flash("Application rejected", "info")
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

# ============================================================================
# SECTION 12: ADMIN ROUTES
# ============================================================================

# ============================================================================
# 12.1 Admin Home
# ============================================================================

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

@app.errorhandler(404)
def page_not_found(error):
    """Handle 404 errors"""
    return render_template("404.html"), 404

@app.errorhandler(403)
def forbidden(error):
    """Handle 403 forbidden"""
    return render_template("403.html"), 403

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 server errors"""
    db.session.rollback()
    return render_template("500.html"), 500

# ============================================================================
# SECTION 14: MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    """
    Run Flask development server.
    
    For production, use a WSGI server like Gunicorn:
    $ gunicorn app:app
    """
    app.run(debug=True, host="0.0.0.0", port=5000)