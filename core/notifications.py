# In-app notifications (bell icon) with optional email for key events.

from flask import Blueprint, abort, current_app, has_request_context, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db

from .mailer import send_email
from .models import Notification, User

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def notify(user_id: int, message: str, link: str = None, *, email: bool = False) -> Notification:
    """Queue a notification in the current session (the caller commits)."""
    if link and not (link.startswith("/") and not link.startswith("//")):
        link = None  # internal paths only
    note = Notification(user_id=user_id, message=message[:300], link=link)
    db.session.add(note)
    if email and current_app.config.get("MAIL_EVENT_EMAILS"):
        user = db.session.get(User, user_id)
        if user is not None:
            origin = base_url()
            where = f"\n\nOpen in STEP: {origin}{link or '/dashboard'}" if origin else "\n\nLog in to STEP to see it."
            send_email(user.email, f"STEP: {message[:80]}",
                       f"Hi {user.name},\n\n{message}{where}\n\n"
                       "You're receiving this because you have a STEP account.")
    return note


def base_url():
    """Origin for absolute links in emails.

    Uses PUBLIC_BASE_URL. Falls back to the request's host only in debug/testing:
    in production the Host header is attacker-controlled, and trusting it would
    let someone receive a victim's password-reset link (reset poisoning).
    Returns None when no trustworthy origin is available.
    """
    configured = current_app.config.get("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    if has_request_context() and (current_app.debug or current_app.config.get("TESTING")):
        return request.url_root.rstrip("/")
    return None


def unread_count(user_id: int) -> int:
    return Notification.query.filter_by(user_id=user_id, is_read=False).count()


@bp.route("")
@login_required
def index():
    notes = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(100).all())
    return render_template("notifications.html", notes=notes)


@bp.route("/<int:note_id>/open", methods=["POST"])
@login_required
def open_note(note_id):
    note = Notification.query.filter_by(id=note_id, user_id=current_user.id).first()
    if note is None:
        abort(404)
    note.is_read = True
    db.session.commit()
    return redirect(note.link or url_for("notifications.index"))


@bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("notifications.index"))


def init_notifications(app) -> None:
    app.register_blueprint(bp)

    @app.context_processor
    def inject_unread():
        if current_user.is_authenticated:
            return {"unread_notifications": unread_count(current_user.id)}
        return {"unread_notifications": 0}
