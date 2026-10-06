# The full STEP work loop and its permissions:
# post -> apply -> select -> submit -> revise -> approve -> rate -> portfolio,
# with notifications and emails along the way.

import io

import pytest
from conftest import CSRF, login, step_app

from core.models import Notification, Review, Submission

Task, Application = step_app.Task, step_app.Application


def _post(client, url, **data):
    return client.post(url, data={"csrf_token": CSRF, **data})


@pytest.fixture()
def people(make_user):
    return {
        "company": make_user("company", email="co@acme.ie", name="Acme"),
        "rival": make_user("company", email="rival@corp.ie", name="Rival"),
        "sam": make_user("student", email="sam@ucc.ie", name="Sam"),
        "alex": make_user("student", email="alex@ucc.ie", name="Alex"),
    }


def _as(client, user):
    client.get("/logout")
    login(client, user)


def _notes(user):
    return [n.message for n in Notification.query.filter_by(user_id=user.id).order_by(Notification.id)]


def test_full_work_loop(app, client, people):
    co, sam, alex = people["company"], people["sam"], people["alex"]

    # Company posts a task with a deadline
    _as(client, co)
    resp = _post(client, "/tasks/new", title="Onboarding research", requirements="Interview 6 users",
                 estimated_hours="30", deadline="2030-01-31")
    task = Task.query.one()
    assert resp.headers["Location"].endswith(f"/company/tasks/{task.id}")
    assert task.status == "open" and task.deadline.isoformat() == "2030-01-31"

    # Two students apply; the company is notified
    for student, note in ((sam, "I've done UX research before"), (alex, "")):
        _as(client, student)
        assert "Onboarding research" in client.get("/tasks").get_data(as_text=True)
        assert _post(client, f"/tasks/{task.id}/apply", cover_note=note).status_code == 302
    assert len(_notes(co)) == 2
    _as(client, sam)
    assert _post(client, f"/tasks/{task.id}/apply").status_code == 302
    assert Application.query.filter_by(student_id=sam.id).count() == 1  # no duplicates

    # Company reviews applicants (profile + note visible) and selects Sam
    _as(client, co)
    page = client.get(f"/company/tasks/{task.id}").get_data(as_text=True)
    assert "done UX research before" in page and "Alex" in page
    sam_app = Application.query.filter_by(student_id=sam.id).one()
    _post(client, f"/company/applications/{sam_app.id}/select")
    step_app.db.session.expire_all()
    assert task.status == "in_progress"
    assert sam_app.status == "accepted"
    assert Application.query.filter_by(student_id=alex.id).one().status == "rejected"
    assert any("selected" in m for m in _notes(sam)) and any("not selected" in m for m in _notes(alex))
    assert app.extensions["outbox"][-1]["To"] == "sam@ucc.ie"  # selection email
    # Task leaves the public board
    _as(client, alex)
    assert "Onboarding research" not in client.get("/tasks").get_data(as_text=True)
    assert client.get(f"/my/tasks/{task.id}").status_code == 404  # not Alex's task

    # Sam submits work with a file
    _as(client, sam)
    resp = client.post(f"/my/tasks/{task.id}", content_type="multipart/form-data", data={
        "csrf_token": CSRF, "message": "Interview notes and summary", "link": "https://drive.example/x",
        "file": (io.BytesIO(b"%PDF-1.4 report"), "report.pdf")})
    assert resp.status_code == 302
    first = Submission.query.one()
    assert first.status == "submitted" and first.file_original_name == "report.pdf"
    # Can't submit again while it's being reviewed
    _post(client, f"/my/tasks/{task.id}", message="again")
    assert Submission.query.count() == 1

    # Company requests changes (feedback required)
    _as(client, co)
    _post(client, f"/company/submissions/{first.id}/review", action="revise", feedback="")
    step_app.db.session.expire_all()
    assert first.status == "submitted"
    _post(client, f"/company/submissions/{first.id}/review", action="revise", feedback="Add 2 more interviews")
    step_app.db.session.expire_all()
    assert first.status == "revision_requested" and first.feedback == "Add 2 more interviews"
    assert any("Changes requested" in m for m in _notes(sam))

    # Sam resubmits; company approves; task completes
    _as(client, sam)
    assert "Add 2 more interviews" in client.get(f"/my/tasks/{task.id}").get_data(as_text=True)
    _post(client, f"/my/tasks/{task.id}", message="Now with 8 interviews")
    second = Submission.query.order_by(Submission.id.desc()).first()
    _as(client, co)
    _post(client, f"/company/submissions/{second.id}/review", action="approve")
    step_app.db.session.expire_all()
    assert task.status == "completed" and task.completed_at is not None
    assert second.status == "approved"

    # Company rates once; the review shows on Sam's portfolio
    _post(client, f"/company/tasks/{task.id}/rate", rating="9")
    assert Review.query.count() == 0
    _post(client, f"/company/tasks/{task.id}/rate", rating="5", comment="Thorough and clear")
    _post(client, f"/company/tasks/{task.id}/rate", rating="1")
    assert Review.query.one().rating == 5
    _as(client, alex)
    portfolio = client.get(f"/students/{sam.id}").get_data(as_text=True)
    assert "Onboarding research" in portfolio and "Thorough and clear" in portfolio and "5.0 average" in portfolio
    assert "Onboarding research" not in client.get(f"/students/{alex.id}").get_data(as_text=True)

    # Completed tasks can't be deleted
    _as(client, co)
    _post(client, f"/tasks/{task.id}/delete")
    assert step_app.db.session.get(Task, task.id) is not None


def _task(company, **kw):
    task = Task(title=kw.pop("title", "T"), company_id=company.id, **kw)
    step_app.db.session.add(task)
    step_app.db.session.commit()
    return task


def test_cannot_apply_to_closed_or_in_progress_tasks(app, client, people):
    co, sam = people["company"], people["sam"]
    closed = _task(co, status="closed")
    busy = _task(co, status="in_progress")
    _as(client, sam)
    for t in (closed, busy):
        _post(client, f"/tasks/{t.id}/apply")
        assert client.get(f"/tasks/{t.id}").status_code == 404
    assert Application.query.count() == 0


def test_withdraw_and_reapply(app, client, people):
    task = _task(people["company"])
    _as(client, people["sam"])
    _post(client, f"/tasks/{task.id}/apply", cover_note="first")
    application = Application.query.one()
    _post(client, f"/applications/{application.id}/withdraw")
    step_app.db.session.expire_all()
    assert application.status == "withdrawn"
    _post(client, f"/tasks/{task.id}/apply")
    step_app.db.session.expire_all()
    assert application.status == "pending" and Application.query.count() == 1


def test_close_and_reopen_notifies_pending(app, client, people):
    co, sam = people["company"], people["sam"]
    task = _task(co)
    _as(client, sam)
    _post(client, f"/tasks/{task.id}/apply")
    _as(client, co)
    _post(client, f"/company/tasks/{task.id}/status", action="close")
    step_app.db.session.expire_all()
    assert task.status == "closed" and Application.query.one().status == "rejected"
    assert any("no longer accepting" in m for m in _notes(sam))
    _post(client, f"/company/tasks/{task.id}/status", action="reopen")
    step_app.db.session.expire_all()
    assert task.status == "open"


def test_other_company_cannot_touch_task(app, client, people):
    co, rival, sam = people["company"], people["rival"], people["sam"]
    task = _task(co)
    _as(client, sam)
    _post(client, f"/tasks/{task.id}/apply")
    application = Application.query.one()
    _as(client, rival)
    assert client.get(f"/company/tasks/{task.id}").status_code == 404
    assert client.get(f"/tasks/{task.id}").status_code == 404
    assert _post(client, f"/company/applications/{application.id}/select").status_code == 404
    assert _post(client, f"/company/applications/{application.id}/reject").status_code == 404
    assert _post(client, f"/company/tasks/{task.id}/status", action="close").status_code == 404
    assert _post(client, f"/company/tasks/{task.id}/rate", rating="1").status_code == 404
    step_app.db.session.expire_all()
    assert application.status == "pending" and task.status == "open"


def test_submission_permissions_and_file_access(app, client, people):
    co, rival, sam, alex = people["company"], people["rival"], people["sam"], people["alex"]
    task = _task(co, status="in_progress")
    application = Application(task_id=task.id, student_id=sam.id, status="accepted")
    step_app.db.session.add(application)
    step_app.db.session.commit()

    _as(client, alex)
    assert _post(client, f"/my/tasks/{task.id}", message="sneaky").status_code == 404
    _as(client, sam)
    client.post(f"/my/tasks/{task.id}", content_type="multipart/form-data",
                data={"csrf_token": CSRF, "message": "Done", "file": (io.BytesIO(b"data"), "work.zip")})
    submission = Submission.query.one()
    url = f"/submissions/{submission.id}/file"

    resp = client.get(url)
    assert resp.status_code == 200 and resp.headers["Content-Type"] == "application/octet-stream"
    assert "attachment" in resp.headers["Content-Disposition"]
    _as(client, co)
    assert client.get(url).status_code == 200
    for outsider in (rival, alex):
        _as(client, outsider)
        assert client.get(url).status_code == 404
    _as(client, rival)
    assert _post(client, f"/company/submissions/{submission.id}/review", action="approve").status_code == 404


@pytest.mark.parametrize("name,payload,message", [
    ("virus.exe", b"MZ", "That file type"),
    ("script.html", b"<script>", "That file type"),
    ("empty.pdf", b"", "empty"),
])
def test_submission_file_validation(app, client, people, name, payload, message):
    task = _task(people["company"], status="in_progress")
    step_app.db.session.add(Application(task_id=task.id, student_id=people["sam"].id, status="accepted"))
    step_app.db.session.commit()
    _as(client, people["sam"])
    resp = client.post(f"/my/tasks/{task.id}", content_type="multipart/form-data",
                       data={"csrf_token": CSRF, "message": "x", "file": (io.BytesIO(payload), name)})
    assert resp.status_code == 400 and message in resp.get_data(as_text=True)
    assert Submission.query.count() == 0


def test_submission_link_must_be_http(app, client, people):
    task = _task(people["company"], status="in_progress")
    step_app.db.session.add(Application(task_id=task.id, student_id=people["sam"].id, status="accepted"))
    step_app.db.session.commit()
    _as(client, people["sam"])
    resp = _post(client, f"/my/tasks/{task.id}", message="x", link="javascript:alert(1)")
    assert resp.status_code == 400 and Submission.query.count() == 0


def test_students_cannot_use_company_routes(app, client, people):
    task = _task(people["company"])
    _as(client, people["sam"])
    for url in (f"/company/tasks/{task.id}", "/tasks/new"):
        assert client.get(url).headers["Location"].endswith("/dashboard")
    assert _post(client, f"/company/tasks/{task.id}/status", action="close").headers["Location"].endswith("/dashboard")


def test_dashboards_surface_work(app, client, people):
    co, sam = people["company"], people["sam"]
    task = _task(co, title="Needs review", status="in_progress")
    application = Application(task_id=task.id, student_id=sam.id, status="accepted")
    step_app.db.session.add(application)
    step_app.db.session.commit()
    step_app.db.session.add(Submission(task_id=task.id, application_id=application.id, student_id=sam.id, message="m"))
    step_app.db.session.commit()
    _as(client, co)
    assert "Work to review" in client.get("/dashboard").get_data(as_text=True)
    _as(client, sam)
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Your active tasks" in html and "Needs review" in html


def test_notifications_read_and_safe_links(app, client, people):
    sam = people["sam"]
    step_app.db.session.add_all([Notification(user_id=sam.id, message="A", link="/my/work"),
                                 Notification(user_id=sam.id, message="B", link="/my/work"),
                                 Notification(user_id=people["alex"].id, message="Not yours", link="/x")])
    step_app.db.session.commit()
    _as(client, sam)
    page = client.get("/notifications").get_data(as_text=True)
    assert "Not yours" not in page and "Mark all as read" in page
    note = Notification.query.filter_by(user_id=sam.id, message="A").one()
    resp = _post(client, f"/notifications/{note.id}/open")
    assert resp.headers["Location"].endswith("/my/work")
    other = Notification.query.filter_by(message="Not yours").one()
    assert _post(client, f"/notifications/{other.id}/open").status_code == 404
    _post(client, "/notifications/read-all")
    assert Notification.query.filter_by(user_id=sam.id, is_read=False).count() == 0

    from core.notifications import notify

    assert notify(sam.id, "x", "https://evil.example").link is None
    assert notify(sam.id, "x", "//evil.example").link is None


def test_admin_deleting_student_reopens_their_task(app, client, people, make_user):
    admin = make_user("admin", email="admin@step.ie")
    task = _task(people["company"], status="in_progress")
    application = Application(task_id=task.id, student_id=people["sam"].id, status="accepted")
    step_app.db.session.add(application)
    step_app.db.session.commit()
    step_app.db.session.add(Submission(task_id=task.id, application_id=application.id, student_id=people["sam"].id, message="m"))
    step_app.db.session.commit()
    _as(client, admin)
    _post(client, f"/admin/users/{people['sam'].id}/delete")
    step_app.db.session.expire_all()
    assert step_app.User.query.filter_by(email="sam@ucc.ie").first() is None
    assert task.status == "open" and Submission.query.count() == 0


def test_dispute_only_allows_own_tasks_and_counterparties(app, client, people):
    co, rival, sam = people["company"], people["rival"], people["sam"]
    mine = _task(co, title="Mine")
    theirs = _task(rival, title="Theirs")
    _as(client, sam)
    _post(client, f"/tasks/{mine.id}/apply")
    page = client.get("/disputes/new").get_data(as_text=True)
    assert "Mine" in page and "Theirs" not in page and "Acme (company)" in page
    _post(client, "/disputes/new", message="Problem", task_id=str(theirs.id), against_user_id=str(rival.id))
    d = step_app.Dispute.query.one()
    assert d.task_id is None and d.against_user_id is None  # not people/tasks Sam worked with
    _post(client, "/disputes/new", message="Real problem", task_id=str(mine.id), against_user_id=str(co.id))
    d = step_app.Dispute.query.order_by(step_app.Dispute.id.desc()).first()
    assert (d.task_id, d.against_user_id) == (mine.id, co.id)
