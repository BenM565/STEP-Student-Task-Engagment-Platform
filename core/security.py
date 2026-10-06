# App-wide security helpers: CSRF protection for every form, password policy,
# safe post-login redirects and role-based route decorators.

import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, redirect, request, session, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

CSRF_SESSION_KEY = "_csrf_token"
MIN_PASSWORD_LENGTH = 8


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field() -> Markup:
    # Hidden input for every POST form: {{ csrf_field() }}
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


def protect_from_csrf():
    """before_request hook: reject state-changing requests without the session's token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(expected, sent):
        abort(400, description="Your session expired or the form was submitted from another site. "
                               "Reload the page and try again.")
    return None


def password_problem(password: str, confirm: str = None):
    """Return a user-facing reason the password is unacceptable, or None."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.strip() != password:
        return "Password cannot start or end with a space."
    if confirm is not None and password != confirm:
        return "The passwords do not match."
    return None


def safe_next_url(target: str):
    """Only allow redirects to a path on this site (prevents open redirects after login)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def role_required(*roles):
    """Restrict a view to logged-in users with one of the given roles."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                flash("You don't have access to that page.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


def init_security(app) -> None:
    app.before_request(protect_from_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["csrf_field"] = csrf_field
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    if app.config.get("SECRET_KEY") in (None, "", "dev", "secret") and not app.config.get("TESTING"):
        app.logger.warning("FLASK_SECRET is missing or a well-known value. Set a long random FLASK_SECRET: "
                           "sessions and password-reset links are signed with it.")
