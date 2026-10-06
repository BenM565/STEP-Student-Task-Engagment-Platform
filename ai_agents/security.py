# Access control for the AI Agents area. CSRF is enforced app-wide by
# core/security.py; this module keeps the AI templates' token helper name.

from flask import current_app, flash, redirect, url_for
from flask_login import current_user

from core.security import csrf_token  # noqa: F401  (exposed to templates as ai_csrf_token)


def guard_company_area():
    """before_request hook: only logged-in company accounts may use AI agents."""
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if getattr(current_user, "role", None) != "company":
        flash("AI agents are available to company accounts only.", "danger")
        return redirect(url_for("dashboard"))
    return None
