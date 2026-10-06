# Account recovery and password management.
# Reset links are signed, expire after PASSWORD_RESET_MAX_AGE seconds and are
# single-use: the token embeds part of the current password hash, so it stops
# working as soon as the password changes.

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import db

from .mailer import send_email
from .models import User
from .notifications import base_url
from .security import password_problem

bp = Blueprint("accounts", __name__)

_SALT = "step-password-reset"
RESET_SENT_MESSAGE = ("If an account exists for that email, we've sent a link to reset the password. "
                      "Check your inbox and spam folder.")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def _fingerprint(user: User) -> str:
    return user.password_hash[-16:]


def make_reset_token(user: User) -> str:
    return _serializer().dumps({"uid": user.id, "fp": _fingerprint(user)})


def user_from_reset_token(token: str):
    """Return the user for a valid, unexpired, unused token; otherwise None."""
    try:
        data = _serializer().loads(token, max_age=int(current_app.config["PASSWORD_RESET_MAX_AGE"]))
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or data.get("fp") != _fingerprint(user):
        return None
    return user


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first() if email else None
        origin = base_url()
        if user is not None and origin is None:
            current_app.logger.error("Password reset requested but PUBLIC_BASE_URL is not set; email not sent. "
                                     "Set PUBLIC_BASE_URL to the site's public address, e.g. https://step.example.com")
        elif user is not None:
            link = origin + url_for("accounts.reset_password", token=make_reset_token(user))
            minutes = int(current_app.config["PASSWORD_RESET_MAX_AGE"]) // 60
            send_email(user.email, "Reset your STEP password",
                       f"Hi {user.name},\n\nSomeone asked to reset the password for your STEP account.\n"
                       f"Use this link within {minutes} minutes:\n\n{link}\n\n"
                       "If you didn't ask for this, you can ignore this email; your password won't change.")
        # Same response whether or not the account exists (no account enumeration)
        flash(RESET_SENT_MESSAGE, "info")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = user_from_reset_token(token)
    if user is None:
        flash("That reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for("accounts.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        problem = password_problem(password, request.form.get("confirm", ""))
        if problem:
            flash(problem, "danger")
            return render_template("reset_password.html", token=token), 400
        user.set_password(password)
        db.session.commit()
        send_email(user.email, "Your STEP password was changed",
                   f"Hi {user.name},\n\nThe password for your STEP account was just changed. "
                   "If this wasn't you, reset it again immediately and contact the STEP team.")
        flash("Your password has been reset. You can now log in.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Your current password is incorrect.", "danger")
            return render_template("change_password.html"), 400
        password = request.form.get("password", "")
        problem = password_problem(password, request.form.get("confirm", ""))
        if problem:
            flash(problem, "danger")
            return render_template("change_password.html"), 400
        current_user.set_password(password)
        db.session.commit()
        flash("Password changed.", "success")
        return redirect(url_for("profile"))
    return render_template("change_password.html")


def init_accounts(app) -> None:
    app.config.setdefault("PASSWORD_RESET_MAX_AGE", 3600)
    app.register_blueprint(bp)
