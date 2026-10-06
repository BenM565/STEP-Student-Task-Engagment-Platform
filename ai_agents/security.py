# Access control and CSRF protection for the AI Agents area.
# The rest of STEP has no CSRF protection; agent runs spend money and touch
# company documents, so every POST in this blueprint requires a token.

import hmac
import secrets

from flask import abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user

_SESSION_KEY = "ai_agents_csrf"


def csrf_token() -> str:
    token = session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def verify_csrf() -> None:
    expected = session.get(_SESSION_KEY)
    sent = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, sent):
        abort(400, description="Your session expired or the form was submitted from another site. Reload the page and try again.")


def guard_company_area():
    """before_request hook: only logged-in company accounts may use AI agents."""
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if getattr(current_user, "role", None) != "company":
        flash("AI agents are available to company accounts only.", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        verify_csrf()
    return None
