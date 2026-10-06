# Whole-system smoke test: every GET page, as every role (and logged out),
# against a database seeded with tasks in every state. Any template or view
# error fails the test (TESTING propagates exceptions).

import io

import pytest
from conftest import CSRF, ba_form, login, step_app

from core.models import Dispute, Notification, Review, Submission, utcnow


@pytest.fixture()
def seeded(app, make_user, client):
    db = step_app.db
    student = make_user("student", email="stu@ucc.ie", name="Sam Student")
    other_student = make_user("student", email="other@ucc.ie", name="Olly Other")
    company = make_user("company", email="co@acme.ie", name="Acme")
    admin = make_user("admin", email="admin@step.ie", name="Ada Admin")
    T, A = step_app.Task, step_app.Application

    open_task = T(title="Open task", requirements="Build a thing", company_id=company.id, status="open")
    progress = T(title="In progress task", company_id=company.id, status="in_progress")
    done = T(title="Completed task", company_id=company.id, status="completed", completed_at=utcnow())
    closed = T(title="Closed task", company_id=company.id, status="closed")
    db.session.add_all([open_task, progress, done, closed])
    db.session.commit()
    db.session.add_all([
        A(task_id=open_task.id, student_id=student.id, status="pending", cover_note="Keen"),
        A(task_id=progress.id, student_id=student.id, status="accepted"),
        A(task_id=progress.id, student_id=other_student.id, status="rejected"),
        A(task_id=done.id, student_id=student.id, status="accepted"),
    ])
    db.session.commit()
    app_progress = A.query.filter_by(task_id=progress.id, student_id=student.id).one()
    app_done = A.query.filter_by(task_id=done.id).one()
    db.session.add_all([
        Submission(task_id=progress.id, application_id=app_progress.id, student_id=student.id, message="v1", status="submitted"),
        Submission(task_id=done.id, application_id=app_done.id, student_id=student.id, message="final", status="approved"),
        Review(task_id=done.id, company_id=company.id, student_id=student.id, rating=5, comment="Great"),
        Dispute(raised_by_user_id=student.id, against_user_id=company.id, task_id=progress.id, message="Late feedback"),
        Notification(user_id=student.id, message="Hello", link="/my/work"),
    ])
    db.session.commit()

    # One AI run for the company
    login(client, company)
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    client.get("/logout")
    return {"student": student, "company": company, "admin": admin, "tasks": [open_task, progress, done, closed]}


def _urls(app, ids):
    """Every GET route, with ids substituted from the seeded data."""
    from ai_agents.models import AgentFile, AgentRun

    run_id = AgentRun.query.first().id
    sample = {"task_id": ids["task"], "student_id": ids["student"], "run_id": run_id, "user_id": ids["student"],
              "dispute_id": 1, "submission_id": 1, "application_id": 1, "note_id": 1,
              "file_id": (AgentFile.query.first().id if AgentFile.query.first() else 1),
              "slug": "business-analyst", "token": "invalid-token", "filename": "x"}
    urls = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", str(sample[arg])).replace(f"<{arg}>", str(sample[arg]))
        urls.append(url)
    return urls


@pytest.mark.parametrize("role", ["anonymous", "student", "company", "admin"])
def test_every_page_renders_for_every_role(app, client, seeded, role):
    if role != "anonymous":
        login(client, seeded[role])
    checked = 0
    for task in seeded["tasks"]:
        for url in _urls(app, {"task": task.id, "student": seeded["student"].id}):
            if url == "/logout":
                continue
            resp = client.get(url)
            assert resp.status_code < 500, f"{role} {url} -> {resp.status_code}"
            checked += 1
    assert checked >= 34 * 4  # every GET route, once per seeded task


def test_key_pages_render_real_content(app, client, seeded):
    s, c, a = seeded["student"], seeded["company"], seeded["admin"]
    open_task, progress, done, _ = seeded["tasks"]

    login(client, a)
    html = client.get("/admin").get_data(as_text=True)
    assert "Admin dashboard" in html and "Students" in html  # previously crashed
    disputes = client.get("/admin/disputes").get_data(as_text=True)
    assert "Late feedback" in disputes and "Sam Student" in disputes  # previously a broken standalone page
    client.get("/logout")

    login(client, c)
    manage = client.get(f"/company/tasks/{progress.id}").get_data(as_text=True)
    assert "Work from Sam Student" in manage and "Approve and complete" in manage
    assert "Rate" not in client.get(f"/company/tasks/{done.id}").get_data(as_text=True).split("Your review")[0][-50:]
    client.get("/logout")

    login(client, s)
    work = client.get("/my/work").get_data(as_text=True)
    assert "In progress task" in work and "Completed task" in work
    portfolio = client.get(f"/students/{s.id}").get_data(as_text=True)
    assert "Completed task" in portfolio and "Great" in portfolio and "5.0 average" in portfolio


def test_navigation_matches_role(app, client, seeded):
    login(client, seeded["student"])
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Find tasks" in html and "My work" in html and "AI Agents" not in html
    client.get("/logout")
    login(client, seeded["company"])
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Post a task" in html and "AI Agents" in html and "Find tasks" not in html
