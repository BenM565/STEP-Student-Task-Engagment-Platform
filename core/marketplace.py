# The STEP work loop after a task is posted:
# student views and applies -> company reviews applicants and selects one ->
# student submits work -> company approves or requests a revision -> company
# rates the student -> the work appears on the student's portfolio.

import hashlib
import os
import uuid
from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_file,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func
from werkzeug.utils import secure_filename

from extensions import db

from .models import Application, Review, Submission, Task, User, utcnow
from .notifications import notify
from .security import role_required

bp = Blueprint("marketplace", __name__)

SUBMISSION_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".zip",
                         ".png", ".jpg", ".jpeg", ".fig", ".sketch", ".json"}
MAX_COVER_NOTE = 2000
MAX_SUBMISSION_MESSAGE = 10000


# ---------------------------------------------------------------------------
# Lookups that enforce ownership
# ---------------------------------------------------------------------------

def _company_task_or_404(task_id: int) -> Task:
    task = Task.query.filter_by(id=task_id, company_id=current_user.id).first()
    if task is None:
        abort(404)
    return task


def _company_application_or_404(application_id: int) -> Application:
    application = (Application.query.join(Task, Task.id == Application.task_id)
                   .filter(Application.id == application_id, Task.company_id == current_user.id).first())
    if application is None:
        abort(404)
    return application


def _accepted_application(task_id: int, student_id: int):
    return Application.query.filter_by(task_id=task_id, student_id=student_id, status="accepted").first()


# ---------------------------------------------------------------------------
# Students: view, apply, withdraw, see their work
# ---------------------------------------------------------------------------

@bp.route("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    if current_user.role == "company":
        if task.company_id == current_user.id:
            return redirect(url_for("marketplace.manage_task", task_id=task.id))
        abort(404)
    if current_user.role == "student":
        application = Application.query.filter_by(task_id=task.id, student_id=current_user.id).first()
        # Students only see open tasks, or tasks they are already involved in
        if task.status != "open" and application is None:
            abort(404)
    else:
        application = None
    return render_template("task_detail.html", task=task, application=application)


@bp.route("/tasks/<int:task_id>/apply", methods=["POST"])
@role_required("student")
def apply(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    if task.status != "open":
        flash("This task is no longer accepting applications.", "warning")
        return redirect(url_for("student_tasks"))
    existing = Application.query.filter_by(task_id=task.id, student_id=current_user.id).first()
    note = (request.form.get("cover_note") or "").strip()[:MAX_COVER_NOTE]
    if existing is not None and existing.status != "withdrawn":
        flash("You have already applied for this task.", "info")
        return redirect(url_for("marketplace.task_detail", task_id=task.id))
    if existing is not None:
        # Re-applying after withdrawing
        existing.status, existing.cover_note = "pending", note or existing.cover_note
    else:
        db.session.add(Application(task_id=task.id, student_id=current_user.id, cover_note=note or None))
    notify(task.company_id, f"New application from {current_user.name} for \"{task.title}\".",
           url_for("marketplace.manage_task", task_id=task.id))
    db.session.commit()
    flash("Application sent. You'll get a notification when the company decides.", "success")
    return redirect(url_for("marketplace.my_work"))


@bp.route("/applications/<int:application_id>/withdraw", methods=["POST"])
@role_required("student")
def withdraw(application_id):
    application = Application.query.filter_by(id=application_id, student_id=current_user.id).first() or abort(404)
    if application.status != "pending":
        flash("Only pending applications can be withdrawn.", "warning")
    else:
        application.status = "withdrawn"
        notify(application.task.company_id,
               f"{current_user.name} withdrew their application for \"{application.task.title}\".",
               url_for("marketplace.manage_task", task_id=application.task_id))
        db.session.commit()
        flash("Application withdrawn.", "success")
    return redirect(url_for("marketplace.my_work"))


@bp.route("/my/work")
@role_required("student")
def my_work():
    applications = (Application.query.filter_by(student_id=current_user.id)
                    .order_by(Application.created_at.desc(), Application.id.desc()).all())
    return render_template("my_work.html", applications=applications)


@bp.route("/my/tasks/<int:task_id>", methods=["GET", "POST"])
@role_required("student")
def student_task(task_id):
    application = _accepted_application(task_id, current_user.id) or abort(404)
    task = application.task
    awaiting_review = any(s.status == "submitted" for s in task.submissions)

    if request.method == "POST":
        if task.status != "in_progress":
            flash("This task is not accepting submissions.", "warning")
            return redirect(url_for("marketplace.student_task", task_id=task.id))
        if awaiting_review:
            flash("Your last submission is still being reviewed.", "warning")
            return redirect(url_for("marketplace.student_task", task_id=task.id))
        message = (request.form.get("message") or "").strip()[:MAX_SUBMISSION_MESSAGE]
        link = (request.form.get("link") or "").strip()[:500]
        upload = request.files.get("file")
        error = None
        if not message:
            error = "Describe what you're submitting."
        elif link and not link.lower().startswith(("https://", "http://")):
            error = "Links must start with https:// or http://"
        stored = None
        if error is None and upload and upload.filename:
            stored, error = _store_submission_file(upload)
        if error:
            flash(error, "danger")
            return render_template("student_task.html", task=task, awaiting_review=awaiting_review,
                                   form={"message": message, "link": link}), 400
        submission = Submission(task_id=task.id, application_id=application.id, student_id=current_user.id,
                                message=message, link=link or None)
        if stored:
            submission.file_stored_name, submission.file_original_name, submission.file_size = stored
        db.session.add(submission)
        notify(task.company_id, f"{current_user.name} submitted work for \"{task.title}\".",
               url_for("marketplace.manage_task", task_id=task.id), email=True)
        db.session.commit()
        flash("Work submitted. The company has been notified.", "success")
        return redirect(url_for("marketplace.student_task", task_id=task.id))

    return render_template("student_task.html", task=task, awaiting_review=awaiting_review, form={})


def _submission_dir() -> str:
    path = current_app.config["SUBMISSION_UPLOAD_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def _store_submission_file(upload):
    """Validate and store an uploaded file. Returns ((stored, original, size), error)."""
    original = secure_filename(upload.filename or "")
    ext = os.path.splitext(original)[1].lower()
    if not original or ext not in SUBMISSION_EXTENSIONS:
        return None, f"That file type isn't accepted. Allowed: {', '.join(sorted(SUBMISSION_EXTENSIONS))}."
    max_bytes = int(current_app.config["SUBMISSION_MAX_FILE_MB"]) * 1024 * 1024
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None, f"Files must be under {current_app.config['SUBMISSION_MAX_FILE_MB']} MB. Share larger work as a link."
    if not data:
        return None, "The file is empty."
    stored = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(_submission_dir(), stored), "wb") as fh:
        fh.write(data)
    current_app.logger.info("Stored submission file %s sha256=%s", stored, hashlib.sha256(data).hexdigest())
    return (stored, upload.filename[:255], len(data)), None


@bp.route("/submissions/<int:submission_id>/file")
@login_required
def submission_file(submission_id):
    submission = db.session.get(Submission, submission_id) or abort(404)
    allowed = (current_user.id == submission.student_id
               or current_user.id == submission.task.company_id
               or current_user.role == "admin")
    if not allowed or not submission.file_stored_name:
        abort(404)
    path = os.path.join(_submission_dir(), submission.file_stored_name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=submission.file_original_name,
                     mimetype="application/octet-stream")


# ---------------------------------------------------------------------------
# Companies: manage a task, select, review, rate, close
# ---------------------------------------------------------------------------

@bp.route("/company/tasks/<int:task_id>")
@role_required("company")
def manage_task(task_id):
    task = _company_task_or_404(task_id)
    applications = sorted(task.applications, key=lambda a: ({"accepted": 0, "pending": 1}.get(a.status, 2), a.id))
    ratings = _rating_summary([a.student_id for a in applications])
    return render_template("manage_task.html", task=task, applications=applications, ratings=ratings,
                           today=date.today())


@bp.route("/company/task/<int:task_id>/applicants")
@role_required("company")
def legacy_applicants(task_id):
    # Old URL from the first STEP iteration
    return redirect(url_for("marketplace.manage_task", task_id=task_id))


@bp.route("/company/applications/<int:application_id>/select", methods=["POST"])
@role_required("company")
def select_candidate(application_id):
    application = _company_application_or_404(application_id)
    task = application.task
    if task.status != "open" or application.status != "pending":
        flash("This applicant can't be selected now.", "warning")
        return redirect(url_for("marketplace.manage_task", task_id=task.id))
    for other in task.applications:
        if other.id != application.id and other.status == "pending":
            other.status = "rejected"
            notify(other.student_id, f"Your application for \"{task.title}\" was not selected this time.",
                   url_for("marketplace.my_work"))
    application.status = "accepted"
    task.status = "in_progress"
    notify(application.student_id, f"You've been selected for \"{task.title}\". You can start work now.",
           url_for("marketplace.student_task", task_id=task.id), email=True)
    db.session.commit()
    flash(f"{application.student.name} selected. Other applicants have been notified.", "success")
    return redirect(url_for("marketplace.manage_task", task_id=task.id))


@bp.route("/company/applications/<int:application_id>/reject", methods=["POST"])
@role_required("company")
def reject_candidate(application_id):
    application = _company_application_or_404(application_id)
    if application.status != "pending":
        flash("Only pending applications can be declined.", "warning")
    else:
        application.status = "rejected"
        notify(application.student_id, f"Your application for \"{application.task.title}\" was not selected this time.",
               url_for("marketplace.my_work"))
        db.session.commit()
        flash("Application declined.", "success")
    return redirect(url_for("marketplace.manage_task", task_id=application.task_id))


@bp.route("/company/submissions/<int:submission_id>/review", methods=["POST"])
@role_required("company")
def review_submission(submission_id):
    submission = (Submission.query.join(Task, Task.id == Submission.task_id)
                  .filter(Submission.id == submission_id, Task.company_id == current_user.id).first()) or abort(404)
    task = submission.task
    action = request.form.get("action")
    feedback = (request.form.get("feedback") or "").strip()[:5000]
    if submission.status != "submitted" or task.status != "in_progress":
        flash("This submission has already been reviewed.", "warning")
    elif action == "revise" and not feedback:
        flash("Explain what needs to change so the student can revise it.", "danger")
    elif action in ("approve", "revise"):
        submission.reviewed_at = utcnow()
        submission.feedback = feedback or None
        if action == "approve":
            submission.status = "approved"
            task.status, task.completed_at = "completed", utcnow()
            notify(submission.student_id, f"Your work on \"{task.title}\" was approved. Well done!",
                   url_for("marketplace.student_task", task_id=task.id), email=True)
            flash("Work approved and task completed. Please rate the student's work.", "success")
        else:
            submission.status = "revision_requested"
            notify(submission.student_id, f"Changes requested on \"{task.title}\".",
                   url_for("marketplace.student_task", task_id=task.id), email=True)
            flash("Revision requested. The student has been notified.", "success")
        db.session.commit()
    else:
        abort(400)
    return redirect(url_for("marketplace.manage_task", task_id=task.id))


@bp.route("/company/tasks/<int:task_id>/rate", methods=["POST"])
@role_required("company")
def rate_student(task_id):
    task = _company_task_or_404(task_id)
    application = task.accepted_application()
    rating = request.form.get("rating", type=int)
    if task.status != "completed" or application is None:
        flash("You can rate the student once the work is approved.", "warning")
    elif task.review is not None:
        flash("You've already rated this task.", "info")
    elif rating not in (1, 2, 3, 4, 5):
        flash("Choose a rating from 1 to 5.", "danger")
    else:
        comment = (request.form.get("comment") or "").strip()[:2000]
        db.session.add(Review(task_id=task.id, company_id=current_user.id, student_id=application.student_id,
                              rating=rating, comment=comment or None))
        notify(application.student_id, f"{current_user.name} left you a {rating}-star review for \"{task.title}\".",
               url_for("marketplace.portfolio", student_id=application.student_id))
        db.session.commit()
        flash("Thanks, your review is on the student's portfolio.", "success")
    return redirect(url_for("marketplace.manage_task", task_id=task.id))


@bp.route("/company/tasks/<int:task_id>/status", methods=["POST"])
@role_required("company")
def change_task_status(task_id):
    task = _company_task_or_404(task_id)
    action = request.form.get("action")
    if action == "close" and task.status == "open":
        task.status = "closed"
        for application in task.applications:
            if application.status == "pending":
                application.status = "rejected"
                notify(application.student_id, f"\"{task.title}\" is no longer accepting applications.",
                       url_for("marketplace.my_work"))
        flash("Task closed to new applications.", "success")
    elif action == "reopen" and task.status == "closed":
        task.status = "open"
        flash("Task reopened for applications.", "success")
    else:
        flash("That change isn't possible for this task.", "warning")
    db.session.commit()
    return redirect(url_for("marketplace.manage_task", task_id=task.id))


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def _rating_summary(student_ids) -> dict:
    if not student_ids:
        return {}
    rows = (db.session.query(Review.student_id, func.avg(Review.rating), func.count(Review.id))
            .filter(Review.student_id.in_(set(student_ids))).group_by(Review.student_id).all())
    return {sid: (round(float(avg), 1), count) for sid, avg, count in rows}


@bp.route("/students/<int:student_id>")
@login_required
def portfolio(student_id):
    student = User.query.filter_by(id=student_id, role="student").first() or abort(404)
    completed = (Task.query.join(Application, Application.task_id == Task.id)
                 .filter(Application.student_id == student.id, Application.status == "accepted",
                         Task.status == "completed")
                 .order_by(Task.completed_at.desc()).all())
    reviews = {r.task_id: r for r in Review.query.filter_by(student_id=student.id).all()}
    summary = _rating_summary([student.id]).get(student.id)
    return render_template("portfolio.html", student=student, completed=completed, reviews=reviews, summary=summary)


def init_marketplace(app) -> None:
    app.config.setdefault("SUBMISSION_UPLOAD_DIR",
                          os.environ.get("SUBMISSION_UPLOAD_DIR") or os.path.join(app.instance_path, "submissions"))
    app.config.setdefault("SUBMISSION_MAX_FILE_MB", int(os.environ.get("SUBMISSION_MAX_FILE_MB", "20")))
    app.register_blueprint(bp)
