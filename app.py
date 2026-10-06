# received bootstrap code from ChatGPT
# for flask SQLAlchemy "Flask SQLAlchemy Tutorial for Database - GeeksforGeeks"
# app route layout from ChatGPT

print("APP.PY STARTED")

import os

from dotenv import load_dotenv
# itteration 1 of the code
from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
    UserMixin,
)
from sqlalchemy import func, \
    text  # use SQL functions inside object relational mapping queries so you don't use SQL anymore; you interact directly with an object in the same language you're using.
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables from a local .env file if present DATABASE_URL FLASK_SECRET
load_dotenv()

# Create a Flask application instance
app = Flask(__name__)

# Configure a secret key for sign session cookies and protection
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "dev")

# Configure SQLAlchemy database URI
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///app.db")


# Disable the SQLAlchemy event system that tracks modifications in memory
# This reduces memory overhead when not needed
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database and login manager extensions
# These must be created after app configuration
login_manager = LoginManager(app)

# Configure the login view endpoint name used by @login_required
login_manager.login_view = "login"

# db lives in extensions.py so other packages (e.g. ai_agents) can share it without importing app.py
from extensions import db

db.init_app(app)

# Database Models


class User(UserMixin, db.Model):
    # Table that stores both student and company accounts
    __tablename__ = "users"

    # Primary key identifier
    id = db.Column(db.Integer, primary_key=True)

    # Role of the user: "student" or "company"
    role = db.Column(db.String(20), nullable=False, default="student")

    # Display name for the account
    name = db.Column(db.String(120), nullable=False)

    # Email address for login; indexed and unique for fast lookup and uniqueness
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)

    # Hashed password using Werkzeug helpers
    password_hash = db.Column(db.String(255), nullable=False)

    # Optional student profile fields
    skills = db.Column(db.Text)
    grades = db.Column(db.String(120))
    projects = db.Column(db.Text)
    references = db.Column(db.Text)

    # Whether the account has been verified based on email rules or manual review
    verified = db.Column(db.Boolean, default=False)

    # Password helper to set a hashed password
    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    # Password helper to verify a plaintext password against the stored hash
    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Task(db.Model):
    # Tasks posted by company users for students to apply to
    __tablename__ = "tasks"

    # Primary key identifier
    id = db.Column(db.Integer, primary_key=True)

    # Short title or summary of the task
    title = db.Column(db.String(200), nullable=False)

    # Detailed requirements or description of the task
    requirements = db.Column(db.Text)

    # Estimated effort for the task in hours
    estimated_hours = db.Column(db.Integer)

    # Foreign key linking the task to the company user that created it
    company_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Backref relationship configured below on Application; optional helper list of applications
    applications = db.relationship(
        "Application", backref="task", cascade="all, delete-orphan"
    )


class Application(db.Model):
    # Applications submitted by student users for a specific task
    __tablename__ = "applications"

    # Primary key identifier
    id = db.Column(db.Integer, primary_key=True)

    # Foreign key to the related task
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)

    # Foreign key to the applying student (stored in users table)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Application workflow status; default is pending
    status = db.Column(db.String(20), default="pending")

    # Server-side timestamp for when the application was created
    created_at = db.Column(db.DateTime, server_default=func.now())



# Login manager user loader

@login_manager.user_loader
def load_user(user_id: str):
    # Flask-Login callback to load a user object by its id
    # Must return None if user is not found
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None


# Helper functions


def is_student_email(email: str) -> bool:
    # Basic line to mark student accounts as verified based on the email domain
    email_lower = (email or "").lower()
    return email_lower.endswith(".ie") or "student" in email_lower


# used ChatGPT for a framework of routes but added info inside to customize website
# Routes
@app.route("/")
def index():
    # Always send users to the real login route
    # Prevents POSTs from hitting '/' which only allows GET
    return redirect(url_for("login"))

@app.after_request
def no_store(response):
    # Prevent caching of authenticated pages and forms
    response.headers["Cache-Control"] = "no-store"
    return response


# register a user account [company/student]
# register a user account [student/company/admin]
@app.route("/register", methods=["GET", "POST"])
def register():
    # Handle account creation for students, companies and admins
    if request.method == "POST":
        role = request.form.get("role", "student").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")

        # Set default values for optional profile fields so they are always defined
        skills = None
        grades = None
        projects = None
        references = None

        # Collect student-only fields when role is student
        if role == "student":
            skills = request.form.get("skills", "").strip()
            grades = request.form.get("grades", "").strip()
            projects = request.form.get("projects", "").strip()
            references = request.form.get("references", "").strip()

        # Collect optional company fields if you later want to use them
        if role == "company":
            skills_needed = request.form.get("company_skills_needed", "").strip()
            comp_task_title = request.form.get("company_task_title", "").strip()
            comp_task_requirements = request.form.get("company_task_requirements", "").strip()
            comp_task_estimate = request.form.get("company_task_estimate", "").strip()
            skills = skills_needed

        # Basic validation: prevent missing critical fields
        if not name or not email or not password:
            flash("Name, email and password are required.", "danger")
            return redirect(url_for("register"))

        # Prevent duplicate registrations by email
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("register"))

        # Create a user record and set an initial verification flag
        user = User(
            role=role,
            name=name,
            email=email,
            skills=skills,
            grades=grades,
            projects=projects,
            references=references,
        )
        # Hash the password before storing it
        user.set_password(password)

        # Mark student accounts as verified based on email rule, others start unverified
        user.verified = is_student_email(email) if role == "student" else False

        # Persist user so they get an id
        db.session.add(user)
        db.session.flush()

        # If a company included a first task in the registration form, create it
        if role == "company":
            comp_task_title = locals().get("comp_task_title", "")
            comp_task_requirements = locals().get("comp_task_requirements", "")
            comp_task_estimate = locals().get("comp_task_estimate", "")
            if comp_task_title:
                try:
                    est_hours = int(comp_task_estimate) if comp_task_estimate else None
                except ValueError:
                    est_hours = None
                task = Task(
                    title=comp_task_title,
                    requirements=comp_task_requirements,
                    estimated_hours=est_hours,
                    company_id=user.id,
                )
                db.session.add(task)

        # Commit all changes
        db.session.commit()

        # If the new account is an admin, log them in immediately and send to their UI
        if role == "admin":
            # Log the new admin user into the current session
            login_user(user)
            flash("Admin account created and logged in.", "success")
            # For now reuse the dashboard view as the admin UI
            return redirect(url_for("dashboard"))

        # All other users are asked to log in normally
        flash("Account created. You can now log in.", "success")
        return redirect(url_for("login"))

    # Render registration page on GET
    return render_template("register.html")



from sqlalchemy import func, or_  #make sure or_ is imported at the top

@app.route("/tasks", methods=["GET"])
@login_required
def student_tasks():
    #only allow student users to see the global task board
    if current_user.role != "student":
        flash("Only students can view the task board.", "danger")
        return redirect(url_for("dashboard"))
    #read filter values from the query string
    skill = (request.args.get("skill") or "").strip()
    max_hours_raw = (request.args.get("max_hours") or "").strip()
    #start with a base query that gets all tasks
    query = Task.query
    #if a skill filter is provided, match it against title or requirements using case-insensitive LIKE
    if skill:
        like_pattern = f"%{skill}%"
        query = query.filter(
            or_(
                Task.title.ilike(like_pattern),
                Task.requirements.ilike(like_pattern),
            )
        )
    #parse the maximum hours filter from the form
    max_hours = None
    if max_hours_raw:
        try:
            max_hours = int(max_hours_raw)
        except ValueError:
            max_hours = None
    #if a valid maximum is provided, limit tasks to that estimated_hours or less
    if max_hours is not None:
        query = query.filter(Task.estimated_hours != None).filter(Task.estimated_hours <= max_hours)
    #execute the query and order results
    tasks = query.order_by(Task.id.asc()).all()
    #render the student task board with the current filters and tasks
    return render_template(
        "student_tasks.html",
        user=current_user,
        tasks=tasks,
        skill=skill,
        max_hours=max_hours_raw,
    )




# login to an existing account
@app.route("/login", methods=["GET", "POST"])
def login():
    # Authenticate a user by verifying email and password
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")





# logout of the current user
@app.route("/logout")
@login_required
def logout():
    # Log out the current user and return to the login page
    logout_user()
    return redirect(url_for("login"))





# company dashboard to see tasks uploaded
@app.route("/dashboard")
@login_required
def dashboard():
    # if the logged in user is an admin, send them to the admin dashboard
    if current_user.role == "admin":
        return redirect(url_for("admin_home"))
    # prepare default values for tasks and application counts
    tasks = []
    counts = {}
    # company dashboard loads tasks created by this company
    if current_user.role == "company":
        tasks = Task.query.filter_by(company_id=current_user.id).all()
        rows = (
            db.session.query(Application.task_id, func.count(Application.id))
            .join(Task, Task.id == Application.task_id)
            .filter(Task.company_id == current_user.id)
            .group_by(Application.task_id)
            .all()
        )
        counts = {task_id: c for task_id, c in rows}
    # student dashboard should show tasks posted by all company users
    elif current_user.role == "student":
        # load a limited number of tasks to show a preview on the dashboard
        tasks = Task.query.order_by(Task.id.asc()).limit(5).all()
    # render a shared dashboard template and pass any tasks and counts found
    return render_template("dashboard.html", user=current_user, tasks=tasks, counts=counts)


# Allows students to apply to tasks
@app.route("/tasks/<int:task_id>/apply", methods=["POST"])
@login_required
def apply(task_id: int):
    # Allow only student users to apply to tasks
    if current_user.role != "student":
        flash("Only students can apply.", "danger")
        return redirect(url_for("dashboard"))

    # Prevent duplicate applications by the same student to the same task
    exists = Application.query.filter_by(task_id=task_id, student_id=current_user.id).first()
    if exists:
        flash("You already applied.", "info")
        return redirect(request.referrer or url_for("dashboard"))

    # Create an application and persist to a database
    appn = Application(task_id=task_id, student_id=current_user.id)
    db.session.add(appn)
    db.session.commit()
    flash("Applied!", "success")
    return redirect(request.referrer or url_for("dashboard"))



# Allows students to update their profile
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    # Allow a user to update their profile and change the password
    if request.method == "POST":
        current_user.name = request.form.get("name", current_user.name).strip()

        # Handle optional password change if a non-empty value was submitted
        new_pw = request.form.get("password", "").strip()
        if new_pw:
            current_user.set_password(new_pw)

        # Allow only students to modify student-specific fields
        if current_user.role == "student":
            current_user.skills = request.form.get("skills", current_user.skills)
            current_user.grades = request.form.get("grades", current_user.grades)
            current_user.projects = request.form.get("projects", current_user.projects)
            current_user.references = request.form.get("references", current_user.references)

        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    # Render profile page for GET requests
    return render_template("profile.html", user=current_user)






# Task creation route for company user
@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id: int):
    # Only company users can delete tasks
    if current_user.role != "company":
        flash("Only company users can delete tasks.", "danger")
        return redirect(url_for("dashboard"))

    task = Task.query.get_or_404(task_id)

    # Prevent deleting tasks that are not owned by this company
    if task.company_id != current_user.id:
        flash("You are not allowed to delete this task.", "danger")
        return redirect(url_for("dashboard"))

    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("dashboard"))





# Companys option to edit tasks
@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id: int):
    # Only company users can edit
    if current_user.role != "company":
        flash("Only company users can edit tasks.", "danger")
        return redirect(url_for("dashboard"))
    # Fetch the task or 404 if missing
    task = Task.query.get_or_404(task_id)
    # Ensure the current company owns the task
    if task.company_id != current_user.id:
        flash("You are not allowed to edit this task.", "danger")
        return redirect(url_for("dashboard"))
    # Handle form submit
    if request.method == "POST":
        # Update fields from form inputs
        title = (request.form.get("title") or "").strip()
        requirements = (request.form.get("requirements") or "").strip()
        est_raw = (request.form.get("estimated_hours") or "").strip()
        if not title:
            flash("Title is required.", "danger")
            return render_template("edit_task.html", task=task)
        try:
            estimated_hours = int(est_raw) if est_raw else None
        except ValueError:
            estimated_hours = None
        task.title = title
        task.requirements = requirements
        task.estimated_hours = estimated_hours
        db.session.commit()
        flash("Task updated.", "success")
        return redirect(url_for("dashboard"))
    # Render form with current values for GET
    return render_template("edit_task.html", task=task)





# Company user can add a new task
@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def add_task():
    # Only company users can add tasks
    if current_user.role != "company":
        flash("Only company users can add tasks.", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        # Extract and sanitize inputs
        title = (request.form.get("title") or "").strip()
        requirements = (request.form.get("requirements") or "").strip()
        est_raw = (request.form.get("estimated_hours") or "").strip()
        # Set when the form was pre-filled from an AI agent result
        source_run = ai_task_source_run(request.form.get("source_run_id", type=int))
        # Basic validation for title
        if not title:
            flash("Title is required.", "danger")
            form = {"title": title, "requirements": requirements, "estimated_hours": est_raw}
            return render_template("add_task.html", form=form, source_run=source_run)
        # Parse estimated hours as integer if provided
        try:
            estimated_hours = int(est_raw) if est_raw else None
        except ValueError:
            estimated_hours = None
        # Create and persist the task row
        task = Task(
            title=title,
            requirements=requirements,
            estimated_hours=estimated_hours,
            company_id=current_user.id,
        )
        db.session.add(task)
        db.session.flush()
        # Link the AI run to the task it produced so the handoff is traceable
        if source_run is not None:
            source_run.created_task_id = task.id
        db.session.commit()
        flash("Task created.", "success")
        return redirect(url_for("dashboard"))
    # GET ?from_run=<id> pre-fills the form from an AI agent's proposed task
    from_run = request.args.get("from_run", type=int)
    if from_run:
        source_run = ai_task_source_run(from_run)
        form = ai_task_form_from_run(source_run)
        if form is None:
            flash("That AI result does not include a task to pre-fill.", "warning")
            return render_template("add_task.html")
        return render_template("add_task.html", form=form, source_run=source_run)
    # Render the blank task form on GET
    return render_template("add_task.html")


def ai_task_source_run(run_id):
    # Return the current company's AI run, or None (never another company's run)
    if not run_id:
        return None
    return AgentRun.query.filter_by(id=run_id, company_id=current_user.id).first()


def ai_task_form_from_run(run):
    # Map an AI agent's proposed task onto this form's fields
    if run is None:
        return None
    agent = get_agent(run.agent_slug)
    if agent is None:
        return None
    output = ai_parsed_output(agent, run)
    draft = agent.task_draft(output) if output is not None else None
    return draft_to_task_form(draft) if draft is not None else None

# iteration 2
# app routes from ChatGPT

#Filter code for tasks filter() in python - GeeksforGeeks

# Dispute model used for raising and managing disputes between users and tasks
class Dispute(db.Model):
    # table name in the database
    __tablename__ = "disputes"
    # primary key for the dispute row
    id = db.Column(db.Integer, primary_key=True)
    # optional link to a task if the dispute is related to a specific task
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    # user id of the person who raised the dispute
    raised_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # optional user id that the dispute is against
    against_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # dispute status in a simple lifecycle: open -> in_review -> resolved
    status = db.Column(db.String(20), nullable=False, default="open")
    # free text message describing the issue
    message = db.Column(db.Text, nullable=False)
    # timestamp when the dispute was created
    created_at = db.Column(db.DateTime, server_default=func.now())
    # timestamp when the dispute was resolved
    resolved_at = db.Column(db.DateTime)
    # admin user id who resolved the dispute
    resolved_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # free text note describing how the dispute was resolved
    resolution_note = db.Column(db.Text)


from functools import wraps
from datetime import datetime  # needed for setting resolved_at

def admin_required(view_func):
    # decorator to restrict a route to admin users only
    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        # check the role field instead of a missing is_admin attribute
        if getattr(current_user, "role", None) != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)
    return wrapper

# admin landing page
@app.route("/admin")
@admin_required
def admin_home():
    # admin landing page collects unverified users and open disputes
    unverified = User.query.filter_by(verified=False, role="student").all()
    open_disputes = Dispute.query.filter_by(status="open").order_by(Dispute.created_at.desc()).all()
    # render the main admin dashboard template with both lists
    return render_template("admin_home.html", unverified=unverified, open_disputes=open_disputes)

# dispute new page
@app.route("/disputes/new", methods=["GET", "POST"])
@login_required
def dispute_new():
    # allow students and companies to open a dispute
    if current_user.role not in ("student", "company"):
        flash("Only students and companies can create disputes.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        # get form fields safely
        task_id_raw = (request.form.get("task_id") or "").strip()
        message = (request.form.get("message") or "").strip()
        against_user_id_raw = (request.form.get("against_user_id") or "").strip()

        # parse integers for foreign keys
        task_id = int(task_id_raw) if task_id_raw.isdigit() else None
        against_user_id = int(against_user_id_raw) if against_user_id_raw.isdigit() else None

        # require a message to describe the issue
        if not message:
            flash("Please describe the issue.", "danger")
            return render_template("dispute_new.html", user=current_user, user_tasks=[])

        # create and store the dispute in the database
        d = Dispute(
            task_id=task_id,
            raised_by_user_id=current_user.id,
            against_user_id=against_user_id,
            message=message,
            status="open",
        )
        db.session.add(d)
        db.session.commit()
        flash("Dispute submitted.", "success")
        return redirect(url_for("dashboard"))

    # build a helpful list of tasks for the dropdown
    user_tasks = []
    if current_user.role == "company":
        # company users see tasks they created
        user_tasks = Task.query.filter_by(company_id=current_user.id).all()
    elif current_user.role == "student":
        # students see tasks they applied for
        task_ids = [a.task_id for a in Application.query.filter_by(student_id=current_user.id).all()]
        if task_ids:
            user_tasks = Task.query.filter(Task.id.in_(task_ids)).all()

    # render the dispute form template
    return render_template("dispute_new.html", user=current_user, user_tasks=user_tasks)

#admin disputes page
@app.route("/admin/disputes")
@admin_required
def admin_disputes():
    # show all disputes to admins ordered with newest first
    all_disputes = Dispute.query.order_by(Dispute.created_at.desc()).all()
    return render_template("admin_disputes.html", disputes=all_disputes)

# admin dispute detail page
@app.route("/admin/disputes/<int:dispute_id>", methods=["GET", "POST"])
@admin_required
def admin_dispute_detail(dispute_id: int):
    # show a single dispute to allow admin to review and update its status
    d = Dispute.query.get_or_404(dispute_id)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("resolution_note") or "").strip()
        # handle resolving the dispute
        if action == "resolve":
            d.status = "resolved"
            d.resolved_at = datetime.utcnow()
            d.resolved_by_admin_id = current_user.id
            d.resolution_note = note
            db.session.commit()
            flash("Dispute resolved.", "success")
            return redirect(url_for("admin_disputes"))
        # handle marking the dispute as in review
        elif action == "in_review":
            d.status = "in_review"
            db.session.commit()
            flash("Dispute marked in review.", "info")
            return redirect(url_for("admin_disputes"))
    # render the detailed view of a single dispute
    return render_template("admin_disputes_detail.html", d=d)

# admin view of all students
@app.route("/admin/users")
@admin_required
def admin_users():
    # show all student accounts so the admin can review them
    students = User.query.filter_by(role="student").order_by(User.id.asc()).all()
    return render_template("admin_users.html", students=students)

# admin view of users details
@app.route("/admin/users/<int:user_id>")
@admin_required
def admin_user_detail(user_id: int):
    # show full details for a single student account
    student = User.query.get_or_404(user_id)
    if student.role != "student":
        flash("This user is not a student account.", "danger")
        return redirect(url_for("admin_users"))
    return render_template("admin_user_detail.html", student=student)

# ability to verify and unverify student accounts
@app.route("/admin/users/<int:user_id>/verify", methods=["POST"])
@admin_required
def admin_verify_user(user_id: int):
    # mark a student account as verified
    student = User.query.get_or_404(user_id)
    if student.role != "student":
        flash("Only student accounts can be verified here.", "danger")
        return redirect(url_for("admin_users"))
    student.verified = True
    db.session.commit()
    flash("Student verified.", "success")
    return redirect(request.referrer or url_for("admin_users"))

# ability to verify and unverify student accounts
@app.route("/admin/users/<int:user_id>/unverify", methods=["POST"])
@admin_required
def admin_unverify_user(user_id: int):
    # remove verification flag from a student account
    student = User.query.get_or_404(user_id)
    if student.role != "student":
        flash("Only student accounts can be updated here.", "danger")
        return redirect(url_for("admin_users"))
    student.verified = False
    db.session.commit()
    flash("Verification removed.", "success")
    return redirect(request.referrer or url_for("admin_users"))

# ability to delete student accounts
@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id: int):
    # Only admin users can delete accounts
    student = User.query.get_or_404(user_id)
    # ensure we only delete student accounts from this view
    if student.role != "student":
        flash("Only student accounts can be deleted from this page.", "danger")
        return redirect(url_for("admin_users"))
    # clean up related applications before deleting the user
    Application.query.filter_by(student_id=student.id).delete()
    # delete the user row
    db.session.delete(student)
    db.session.commit()
    flash("Student account deleted.", "success")
    return redirect(url_for("admin_users"))

#browse tasks page
@app.route("/tasks/browse", methods=["GET"])
@login_required
def browse_tasks():
    # only allow student users to browse and filter tasks
    if current_user.role != "student":
        flash("Only students can browse tasks.", "danger")
        return redirect(url_for("dashboard"))
    # start with a base query that returns all tasks
    query = Task.query
    # read filter values from query string parameters ?skill=...&max_hours=...
    skill = (request.args.get("skill") or "").strip()
    max_hours_raw = (request.args.get("max_hours") or "").strip()
    # if a skill filter is provided, match it against task title or requirements using case-insensitive LIKE
    if skill:
        like_pattern = f"%{skill}%"
        query = query.filter(
            db.or_(
                Task.title.ilike(like_pattern),
                Task.requirements.ilike(like_pattern),
            )
        )
    # if a maximum timeframe (estimated hours) is provided, filter tasks by that upper bound
    max_hours = None
    if max_hours_raw:
        try:
            max_hours = int(max_hours_raw)
        except ValueError:
            max_hours = None
    if max_hours is not None:
        query = query.filter(Task.estimated_hours != None).filter(Task.estimated_hours <= max_hours)
    # execute the query and order results by id for a stable view
    tasks = query.order_by(Task.id.asc()).all()
    # render the browse template and pass current filters and the list of tasks
    return render_template(
        "browse_tasks.html",
        user=current_user,
        tasks=tasks,
        skill=skill,
        max_hours=max_hours_raw,
    )

# itteration 3
@app.route("/company/task/<int:task_id>/applicants")
@login_required
def view_applicants(task_id):
    if current_user.role != "company":
        flash("Company access only.", "danger")
        return redirect(url_for("dashboard"))

    # Only the company that owns the task may see its applicants
    if not Task.query.filter_by(id=task_id, company_id=current_user.id).first():
        abort(404)

    applicants = db.session.execute(
        text("""
        SELECT a.id, u.name, u.email, a.status
        FROM applications a
        JOIN users u ON a.student_id = u.id
        WHERE a.task_id = :task_id
        """),
        {"task_id": task_id}
    ).fetchall()

    return render_template(
        "company_applicants.html",
        applicants=applicants,
        task_id=task_id
    )


@app.route("/company/application/<int:application_id>/select")
@login_required
def select_candidate(application_id):
    if current_user.role != "company":
        flash("Company access only.", "danger")
        return redirect(url_for("dashboard"))

    task_id = db.session.execute(
        text("SELECT task_id FROM applications WHERE id = :id"),
        {"id": application_id}
    ).scalar()

    # Only the company that owns the task may select a candidate for it
    if task_id is None or not Task.query.filter_by(id=task_id, company_id=current_user.id).first():
        abort(404)

    db.session.execute(
        text("""
        UPDATE applications
        SET status = 'rejected'
        WHERE task_id = :task_id
        """),
        {"task_id": task_id}
    )

    db.session.execute(
        text("""
        UPDATE applications
        SET status = 'accepted'
        WHERE id = :id
        """),
        {"id": application_id}
    )

    db.session.commit()

    flash("Candidate selected.", "success")
    return redirect(url_for("view_applicants", task_id=task_id))


# AI Agents (ai_agents/ package): catalogue, workspace, run history and the
# AI -> STEP task handoff used by add_task above
from ai_agents import draft_to_task_form, init_ai_agents
from ai_agents.models import AgentRun
from ai_agents.registry import get_agent
from ai_agents.schema import ensure_schema as ai_ensure_schema
from ai_agents.service import parsed_output as ai_parsed_output, sync_agent_records

init_ai_agents(app, task_model=Task)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Adds AI columns introduced after the AI tables were first created
        ai_ensure_schema()
        sync_agent_records()
    app.run(debug=True)




