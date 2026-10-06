# received bootstrap code from ChatGPT
# for flask SQLAlchemy "Flask SQLAlchemy Tutorial for Database - GeeksforGeeks"
# app route layout from ChatGPT

print("APP.PY STARTED")

import os
from datetime import datetime

import click

from dotenv import load_dotenv
# itteration 1 of the code
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from sqlalchemy import func  # use SQL functions inside object relational mapping queries

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

# Database Models live in core/models.py so blueprints can share them
from core.models import Application, Dispute, Notification, Review, Submission, Task, User, utcnow
from core.security import init_security, password_problem, role_required, safe_next_url

init_security(app)


# Login manager user loader

@login_manager.user_loader
def load_user(user_id: str):
    # Flask-Login callback to load a user object by its id
    # Must return None if user is not found
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# Helper functions


def is_student_email(email: str) -> bool:
    # Basic line to mark student accounts as verified based on the email domain
    email_lower = (email or "").lower()
    return email_lower.endswith(".ie") or "student" in email_lower


def parse_deadline(raw: str):
    # Optional task deadline from an <input type="date">; returns (date or None, error or None)
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        value = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None, "Deadline must be a valid date."
    return value, None


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
        # Admin accounts are created with `flask --app app create-admin`, never by public sign-up
        if role not in ("student", "company"):
            flash("Choose a student or company account.", "danger")
            return redirect(url_for("register"))
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
        problem = password_problem(password)
        if problem:
            flash(problem, "danger")
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
    #start with a base query that gets tasks still accepting applications
    query = Task.query.filter(Task.status == "open")
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
    #execute the query and order results, newest first
    tasks = query.order_by(Task.id.desc()).all()
    #tasks this student already has an active application for
    applied = {a.task_id: a.status for a in Application.query.filter_by(student_id=current_user.id).all()
               if a.status != "withdrawn"}
    #render the student task board with the current filters and tasks
    return render_template(
        "student_tasks.html",
        user=current_user,
        tasks=tasks,
        applied=applied,
        skill=skill,
        max_hours=max_hours_raw,
    )




# login to an existing account
@app.route("/login", methods=["GET", "POST"])
def login():
    # Authenticate a user by verifying email and password
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(safe_next_url(request.args.get("next")) or url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")





# logout of the current user
@app.route("/logout", methods=["GET", "POST"])
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
    context = {}
    # company dashboard loads tasks created by this company
    if current_user.role == "company":
        tasks = Task.query.filter_by(company_id=current_user.id).order_by(Task.id.desc()).all()
        rows = (
            db.session.query(Application.task_id, func.count(Application.id))
            .join(Task, Task.id == Application.task_id)
            .filter(Task.company_id == current_user.id, Application.status == "pending")
            .group_by(Application.task_id)
            .all()
        )
        counts = {task_id: c for task_id, c in rows}
        # submissions waiting for this company's review
        context["awaiting_review"] = {
            task_id for (task_id,) in db.session.query(Submission.task_id)
            .join(Task, Task.id == Submission.task_id)
            .filter(Task.company_id == current_user.id, Submission.status == "submitted").all()
        }
    # student dashboard shows their work plus a preview of open tasks
    elif current_user.role == "student":
        applications = Application.query.filter_by(student_id=current_user.id).all()
        context["active"] = [a for a in applications if a.status == "accepted" and a.task.status == "in_progress"]
        context["pending"] = [a for a in applications if a.status == "pending"]
        applied_ids = {a.task_id for a in applications if a.status != "withdrawn"}
        open_tasks = Task.query.filter(Task.status == "open").order_by(Task.id.desc()).limit(20).all()
        tasks = [t for t in open_tasks if t.id not in applied_ids][:5]
    return render_template("dashboard.html", user=current_user, tasks=tasks, counts=counts, **context)


# Allows students to update their profile
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    # Allow a user to update their profile and change the password
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name is required.", "danger")
            return redirect(url_for("profile"))
        current_user.name = name[:120]

        # Password changes go through /account/password, which checks the current password

        # Allow only students to modify student-specific fields
        if current_user.role == "student":
            current_user.skills = request.form.get("skills", current_user.skills)
            current_user.grades = request.form.get("grades", current_user.grades)
            current_user.projects = request.form.get("projects", current_user.projects)
            current_user.references = request.form.get("references", current_user.references)

        # Company profile shown to students on task pages
        if current_user.role == "company":
            current_user.about = (request.form.get("about") or "").strip()[:5000] or None
            website = (request.form.get("website") or "").strip()[:255]
            if website and not website.lower().startswith(("https://", "http://")):
                website = "https://" + website
            current_user.website = website or None

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

    task = db.get_or_404(Task, task_id)

    # Prevent deleting tasks that are not owned by this company
    if task.company_id != current_user.id:
        flash("You are not allowed to delete this task.", "danger")
        return redirect(url_for("dashboard"))

    # Keep the record once a student has been selected; close the task instead
    if task.status in ("in_progress", "completed"):
        flash("A task with a selected student can't be deleted.", "warning")
        return redirect(url_for("marketplace.manage_task", task_id=task.id))

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
    task = db.get_or_404(Task, task_id)
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
        deadline, deadline_error = parse_deadline(request.form.get("deadline"))
        if not title or deadline_error:
            flash(deadline_error or "Title is required.", "danger")
            return render_template("edit_task.html", task=task)
        try:
            estimated_hours = int(est_raw) if est_raw else None
        except ValueError:
            estimated_hours = None
        task.title = title
        task.requirements = requirements
        task.estimated_hours = estimated_hours
        task.deadline = deadline
        db.session.commit()
        flash("Task updated.", "success")
        return redirect(url_for("marketplace.manage_task", task_id=task.id))
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
        deadline, deadline_error = parse_deadline(request.form.get("deadline"))
        # Basic validation for title
        if not title or deadline_error:
            flash(deadline_error or "Title is required.", "danger")
            form = {"title": title, "requirements": requirements, "estimated_hours": est_raw,
                    "deadline": request.form.get("deadline", "")}
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
            deadline=deadline,
            company_id=current_user.id,
        )
        db.session.add(task)
        db.session.flush()
        # Link the AI run to the task it produced so the handoff is traceable
        if source_run is not None:
            source_run.created_task_id = task.id
        db.session.commit()
        flash("Task created. Students can now apply.", "success")
        return redirect(url_for("marketplace.manage_task", task_id=task.id))
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

# decorator to restrict a route to admin users only
admin_required = role_required("admin")

# admin landing page
@app.route("/admin")
@admin_required
def admin_home():
    # admin landing page collects unverified users and open disputes
    unverified = User.query.filter_by(verified=False, role="student").all()
    open_disputes = Dispute.query.filter_by(status="open").order_by(Dispute.created_at.desc()).all()
    # platform totals for the overview row
    stats = {
        "students": User.query.filter_by(role="student").count(),
        "companies": User.query.filter_by(role="company").count(),
        "open_tasks": Task.query.filter_by(status="open").count(),
        "in_progress": Task.query.filter_by(status="in_progress").count(),
        "completed": Task.query.filter_by(status="completed").count(),
    }
    # render the main admin dashboard template with both lists
    return render_template("admin_home.html", unverified=unverified, open_disputes=open_disputes, stats=stats)

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
        user_tasks, counterparties = dispute_choices()
        # only allow tasks and people this user actually worked with
        if task_id is not None and task_id not in {t.id for t in user_tasks}:
            task_id = None
        if against_user_id is not None and against_user_id not in {u.id for u in counterparties}:
            against_user_id = None

        # require a message to describe the issue
        if not message:
            flash("Please describe the issue.", "danger")
            return render_template("dispute_new.html", user=current_user, user_tasks=user_tasks,
                                   counterparties=counterparties)

        # create and store the dispute in the database
        d = Dispute(
            task_id=task_id,
            raised_by_user_id=current_user.id,
            against_user_id=against_user_id,
            message=message[:5000],
            status="open",
        )
        db.session.add(d)
        db.session.commit()
        flash("Dispute submitted. An admin will review it.", "success")
        return redirect(url_for("dashboard"))

    # build helpful dropdowns of related tasks and people
    user_tasks, counterparties = dispute_choices()
    return render_template("dispute_new.html", user=current_user, user_tasks=user_tasks,
                           counterparties=counterparties)


def dispute_choices():
    # Tasks the current user is involved in, and the other party on each
    if current_user.role == "company":
        user_tasks = Task.query.filter_by(company_id=current_user.id).all()
        people = {a.student for t in user_tasks for a in t.applications if a.status in ("accepted", "rejected", "pending")}
    else:
        applications = Application.query.filter_by(student_id=current_user.id).all()
        user_tasks = [a.task for a in applications]
        people = {t.company for t in user_tasks}
    return user_tasks, sorted((p for p in people if p is not None), key=lambda u: u.name.lower())


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
    d = db.get_or_404(Dispute, dispute_id)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("resolution_note") or "").strip()
        # handle resolving the dispute
        if action == "resolve":
            d.status = "resolved"
            d.resolved_at = utcnow()
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
    student = db.get_or_404(User, user_id)
    if student.role != "student":
        flash("This user is not a student account.", "danger")
        return redirect(url_for("admin_users"))
    return render_template("admin_user_detail.html", student=student)

# ability to verify and unverify student accounts
@app.route("/admin/users/<int:user_id>/verify", methods=["POST"])
@admin_required
def admin_verify_user(user_id: int):
    # mark a student account as verified
    student = db.get_or_404(User, user_id)
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
    student = db.get_or_404(User, user_id)
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
    student = db.get_or_404(User, user_id)
    # ensure we only delete student accounts from this view
    if student.role != "student":
        flash("Only student accounts can be deleted from this page.", "danger")
        return redirect(url_for("admin_users"))
    # reopen tasks this student was working on, then remove their dependent records
    for application in Application.query.filter_by(student_id=student.id, status="accepted").all():
        if application.task.status == "in_progress":
            application.task.status = "open"
    Submission.query.filter_by(student_id=student.id).delete()
    Review.query.filter_by(student_id=student.id).delete()
    Notification.query.filter_by(user_id=student.id).delete()
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
    # the browse page duplicated /tasks; keep the URL working with the same filters
    return redirect(url_for("student_tasks", **request.args))

# itteration 3: applicants, selection, submissions, reviews and portfolios live in core/marketplace.py

# AI Agents (ai_agents/ package): catalogue, workspace, run history and the
# AI -> STEP task handoff used by add_task above
from ai_agents import draft_to_task_form, init_ai_agents
from ai_agents.models import AgentRun
from ai_agents.registry import get_agent
from ai_agents.schema import ensure_schema as ai_ensure_schema
from ai_agents.service import parsed_output as ai_parsed_output, sync_agent_records

init_ai_agents(app, task_model=Task)

# Core platform modules: email, password reset, notifications, the work loop
from core.accounts import init_accounts
from core.mailer import configure_mail
from core.marketplace import init_marketplace
from core.notifications import init_notifications
from core.schema import ensure_core_schema

configure_mail(app)
init_accounts(app)
init_notifications(app)
init_marketplace(app)


def setup_database():
    # Create missing tables and add columns introduced since the database was created
    db.create_all()
    ensure_core_schema()
    ai_ensure_schema()
    sync_agent_records()


@app.cli.command("init-db")
def init_db_command():
    """Create or upgrade all STEP tables."""
    setup_database()
    print("Database is up to date.")


@app.cli.command("create-admin")
@click.option("--email", prompt=True)
@click.option("--name", prompt=True)
@click.password_option()
def create_admin_command(email, name, password):
    """Create an admin account (admins cannot sign up through the website)."""
    email = email.strip().lower()
    problem = password_problem(password)
    if problem:
        raise click.ClickException(problem)
    if User.query.filter_by(email=email).first():
        raise click.ClickException("An account with that email already exists.")
    admin = User(role="admin", name=name.strip(), email=email, verified=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f"Admin {email} created.")


if __name__ == "__main__":
    with app.app_context():
        setup_database()
    app.run(debug=True)




