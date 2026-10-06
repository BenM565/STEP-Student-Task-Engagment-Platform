# Registration, login, CSRF, password policy, password reset and admin creation.

import re

import pytest
from conftest import CSRF, login, step_app
from markupsafe import escape

from core.accounts import RESET_SENT_MESSAGE, make_reset_token


def _seed_csrf(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = CSRF


def _register(client, **overrides):
    _seed_csrf(client)
    data = {"csrf_token": CSRF, "role": "student", "name": "Sam", "email": "sam@ucc.ie", "password": "longenough1"}
    data.update(overrides)
    return client.post("/register", data=data)


# --- registration ----------------------------------------------------------

def test_student_and_company_can_register(app, client):
    assert _register(client).headers["Location"].endswith("/login")
    assert _register(client, role="company", email="co@acme.ie").headers["Location"].endswith("/login")
    student = step_app.User.query.filter_by(email="sam@ucc.ie").one()
    assert student.role == "student" and student.verified is True  # .ie student email rule
    assert student.password_hash != "longenough1"


def test_public_admin_registration_is_blocked(app, client):
    _register(client, role="admin", email="evil@x.com")
    assert step_app.User.query.filter_by(email="evil@x.com").first() is None


def test_short_password_rejected(app, client):
    _register(client, password="short")
    assert step_app.User.query.count() == 0


def test_duplicate_email_rejected(app, client):
    _register(client)
    _register(client, name="Other")
    assert step_app.User.query.count() == 1


# --- login / logout / CSRF ---------------------------------------------------

def test_login_logout_and_safe_next(app, client, make_user):
    user = make_user("student", email="s@ucc.ie")
    _seed_csrf(client)
    resp = client.post("/login?next=/my/work", data={"csrf_token": CSRF, "email": "s@ucc.ie", "password": "pw"})
    assert resp.headers["Location"].endswith("/my/work")
    client.post("/logout", data={"csrf_token": CSRF})
    assert client.get("/dashboard").status_code == 302

    # open redirect attempts fall back to the dashboard
    for evil in ("https://evil.com", "//evil.com", "javascript:alert(1)"):
        _seed_csrf(client)
        resp = client.post(f"/login?next={evil}", data={"csrf_token": CSRF, "email": user.email, "password": "pw"})
        assert resp.headers["Location"].endswith("/dashboard"), evil
        client.post("/logout", data={"csrf_token": CSRF})


def test_wrong_password_rejected(app, client, make_user):
    make_user("student", email="s@ucc.ie")
    _seed_csrf(client)
    resp = client.post("/login", data={"csrf_token": CSRF, "email": "s@ucc.ie", "password": "nope"})
    assert resp.status_code == 200 and "Invalid email or password" in resp.get_data(as_text=True)


@pytest.mark.parametrize("path,data", [
    ("/login", {"email": "x@y.z", "password": "pw"}),
    ("/register", {"role": "student", "name": "n", "email": "n@x.ie", "password": "longenough1"}),
    ("/forgot-password", {"email": "x@y.z"}),
])
def test_forms_reject_missing_csrf(app, client, path, data):
    assert client.post(path, data=data).status_code == 400


def test_logged_in_forms_reject_missing_csrf(app, client, make_user):
    company = make_user("company")
    login(client, company)
    assert client.post("/tasks/new", data={"title": "x"}).status_code == 400
    assert client.post("/profile", data={"name": "x"}, headers={}).status_code == 400
    assert step_app.Task.query.count() == 0


def test_security_headers(app, client):
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"


# --- password reset ----------------------------------------------------------

def _reset_link(app):
    body = app.extensions["outbox"][-1].get_content()
    return re.search(r"http://localhost(/reset-password/\S+)", body).group(1)


def test_password_reset_end_to_end(app, client, make_user):
    user = make_user("student", email="s@ucc.ie")
    _seed_csrf(client)
    resp = client.post("/forgot-password", data={"csrf_token": CSRF, "email": "S@UCC.ie "}, follow_redirects=True)
    assert str(escape(RESET_SENT_MESSAGE)) in resp.get_data(as_text=True)
    assert app.extensions["outbox"][-1]["To"] == "s@ucc.ie"

    link = _reset_link(app)
    assert client.get(link).status_code == 200
    _seed_csrf(client)
    resp = client.post(link, data={"csrf_token": CSRF, "password": "brand-new-pass", "confirm": "brand-new-pass"})
    assert resp.headers["Location"].endswith("/login")
    step_app.db.session.refresh(user)
    assert user.check_password("brand-new-pass") and not user.check_password("pw")
    assert "password was changed" in app.extensions["outbox"][-1]["Subject"]

    # the link is single-use
    resp = client.get(link)
    assert resp.status_code == 302 and "forgot-password" in resp.headers["Location"]
    login(client, user, password="brand-new-pass")


def test_reset_does_not_reveal_unknown_emails(app, client):
    _seed_csrf(client)
    resp = client.post("/forgot-password", data={"csrf_token": CSRF, "email": "nobody@x.com"}, follow_redirects=True)
    assert str(escape(RESET_SENT_MESSAGE)) in resp.get_data(as_text=True)
    assert app.extensions["outbox"] == []


def test_reset_rejects_mismatch_and_short_passwords(app, client, make_user):
    user = make_user("student")
    token = make_reset_token(user)
    _seed_csrf(client)
    resp = client.post(f"/reset-password/{token}", data={"csrf_token": CSRF, "password": "longenough1", "confirm": "different1"})
    assert resp.status_code == 400 and "do not match" in resp.get_data(as_text=True)
    resp = client.post(f"/reset-password/{token}", data={"csrf_token": CSRF, "password": "short", "confirm": "short"})
    assert resp.status_code == 400
    step_app.db.session.refresh(user)
    assert user.check_password("pw")


def test_expired_and_tampered_tokens_rejected(app, client, make_user):
    user = make_user("student")
    token = make_reset_token(user)
    app.config["PASSWORD_RESET_MAX_AGE"] = -1
    assert "forgot-password" in client.get(f"/reset-password/{token}").headers["Location"]
    app.config["PASSWORD_RESET_MAX_AGE"] = 3600
    assert "forgot-password" in client.get(f"/reset-password/{token}x").headers["Location"]
    assert client.get(f"/reset-password/{token}").status_code == 200


def test_reset_links_never_use_untrusted_host_in_production(app, client, make_user):
    make_user("student", email="s@ucc.ie")
    evil = "http://evil.example"
    # the session cookie must belong to the spoofed host, or CSRF would (correctly) reject the request
    with client.session_transaction(base_url=evil) as sess:
        sess["_csrf_token"] = CSRF
    app.config["TESTING"] = False
    try:
        resp = client.post("/forgot-password", data={"csrf_token": CSRF, "email": "s@ucc.ie"}, base_url=evil)
    finally:
        app.config["TESTING"] = True
    assert resp.status_code == 302  # request handled, not rejected
    # No PUBLIC_BASE_URL in production: no email at all rather than a link to the attacker's host
    assert app.extensions["outbox"] == []

    app.config["PUBLIC_BASE_URL"] = "https://step.example.ie"
    resp = client.post("/forgot-password", data={"csrf_token": CSRF, "email": "s@ucc.ie"}, base_url=evil)
    assert resp.status_code == 302
    body = app.extensions["outbox"][-1].get_content()
    assert "https://step.example.ie/reset-password/" in body and "evil.example" not in body


# --- change password and profile -------------------------------------------

def test_change_password_requires_current_password(app, client, make_user):
    user = make_user("company")
    login(client, user)
    resp = client.post("/account/password", data={"csrf_token": CSRF, "current_password": "wrong",
                                                    "password": "newpassword1", "confirm": "newpassword1"})
    assert resp.status_code == 400
    resp = client.post("/account/password", data={"csrf_token": CSRF, "current_password": "pw",
                                                    "password": "newpassword1", "confirm": "newpassword1"})
    assert resp.status_code == 302
    step_app.db.session.refresh(user)
    assert user.check_password("newpassword1")


def test_profile_no_longer_changes_password(app, client, make_user):
    user = make_user("student")
    login(client, user)
    client.post("/profile", data={"csrf_token": CSRF, "name": "New Name", "password": "hijacked1"})
    step_app.db.session.refresh(user)
    assert user.name == "New Name" and user.check_password("pw")


def test_company_profile_fields(app, client, make_user):
    company = make_user("company")
    login(client, company)
    client.post("/profile", data={"csrf_token": CSRF, "name": "Acme", "about": "We make things", "website": "acme.ie"})
    step_app.db.session.refresh(company)
    assert company.about == "We make things" and company.website == "https://acme.ie"


# --- admin -------------------------------------------------------------------

def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--email", "Boss@STEP.ie", "--name", "Boss", "--password", "supersecret1"])
    assert result.exit_code == 0, result.output
    admin = step_app.User.query.filter_by(email="boss@step.ie").one()
    assert admin.role == "admin" and admin.check_password("supersecret1")
    weak = runner.invoke(args=["create-admin", "--email", "b@x.ie", "--name", "B", "--password", "short"])
    assert weak.exit_code != 0


def test_admin_pages_admin_only(app, client, make_user):
    login(client, make_user("company"))
    for url in ("/admin", "/admin/users", "/admin/disputes"):
        assert client.get(url).headers["Location"].endswith("/dashboard")
