# STEP core database models.
# User, Task, Application and Dispute moved here unchanged from app.py (same
# tables and columns) so blueprints can share them; new columns are nullable or
# defaulted and are added to existing databases by core/schema.py.

from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utcnow() -> datetime:
    # Naive UTC, matching how STEP's DATETIME columns are stored
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    # Table that stores student, company and admin accounts
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # "student", "company" or "admin"
    role = db.Column(db.String(20), nullable=False, default="student")
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    # Student profile fields
    skills = db.Column(db.Text)
    grades = db.Column(db.String(120))
    projects = db.Column(db.Text)
    references = db.Column(db.Text)

    # Company profile fields
    about = db.Column(db.Text)
    website = db.Column(db.String(255))

    # Whether the account has been verified based on email rules or manual review
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


# Task lifecycle: open (accepting applications) -> in_progress (student selected)
# -> completed (company approved the work). "closed" = withdrawn by the company.
TASK_STATUSES = ("open", "in_progress", "completed", "closed")


class Task(db.Model):
    # Tasks posted by company users for students to apply to
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    requirements = db.Column(db.Text)
    estimated_hours = db.Column(db.Integer)
    company_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="open", server_default="open", index=True)
    deadline = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)

    company = db.relationship("User", foreign_keys=[company_id])
    applications = db.relationship("Application", backref="task", cascade="all, delete-orphan")
    submissions = db.relationship("Submission", backref="task", cascade="all, delete-orphan",
                                  order_by="Submission.created_at")
    review = db.relationship("Review", backref="task", uselist=False, cascade="all, delete-orphan")

    def accepted_application(self):
        return next((a for a in self.applications if a.status == "accepted"), None)


class Application(db.Model):
    # Applications submitted by student users for a specific task
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # pending -> accepted | rejected; or withdrawn by the student
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, server_default=func.now())
    # Optional message from the student to the company
    cover_note = db.Column(db.Text)

    student = db.relationship("User", foreign_keys=[student_id])


class Dispute(db.Model):
    # Disputes raised by users for admins to resolve
    __tablename__ = "disputes"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    raised_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    against_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # open -> in_review -> resolved
    status = db.Column(db.String(20), nullable=False, default="open")
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())
    resolved_at = db.Column(db.DateTime)
    resolved_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolution_note = db.Column(db.Text)

    task = db.relationship("Task")
    raised_by = db.relationship("User", foreign_keys=[raised_by_user_id])
    against = db.relationship("User", foreign_keys=[against_user_id])


class Submission(db.Model):
    # A piece of work handed in by the selected student. Each revision is a new row.
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False, index=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500))
    # Optional attached file, stored under a random name outside /static
    file_stored_name = db.Column(db.String(64), unique=True)
    file_original_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    # submitted -> approved | revision_requested
    status = db.Column(db.String(30), nullable=False, default="submitted")
    feedback = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime)

    student = db.relationship("User", foreign_keys=[student_id])


class Review(db.Model):
    # The company's rating of the student once a task is completed (one per task)
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False, unique=True)
    company_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Notification(db.Model):
    # In-app notification shown under the bell icon
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message = db.Column(db.String(300), nullable=False)
    # Internal path only (validated in notifications.py)
    link = db.Column(db.String(300))
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
