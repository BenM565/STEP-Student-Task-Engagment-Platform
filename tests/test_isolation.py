# Company isolation: company B must not read, change, export, rerun, download
# or turn into a task anything belonging to company A.

import io

from conftest import CSRF, ba_form, login, step_app

from ai_agents.models import AgentFile, AgentRun


def _company_a_run(client, make_user):
    a = make_user(email="a@corp.com")
    login(client, a)
    client.post("/company/ai-agents/business-analyst",
                data={**ba_form(), "files": [(io.BytesIO(b"Confidential A roadmap"), "roadmap.txt")]},
                content_type="multipart/form-data")
    client.get("/logout")
    return a, AgentRun.query.one()


def test_other_company_gets_404_everywhere(app, client, make_user):
    _, run = _company_a_run(client, make_user)
    file_id = AgentFile.query.one().id
    login(client, make_user(email="b@corp.com"))

    for url in (f"/company/ai-agents/runs/{run.id}",
                f"/company/ai-agents/runs/{run.id}/edit",
                f"/company/ai-agents/runs/{run.id}/export?format=md",
                f"/company/ai-agents/runs/{run.id}/export?format=json",
                f"/company/ai-agents/files/{file_id}",
                f"/company/ai-agents/business-analyst?rerun={run.id}"):
        assert client.get(url).status_code == 404, url

    assert client.post(f"/company/ai-agents/runs/{run.id}/save", data={"csrf_token": CSRF}).status_code == 404
    assert client.post(f"/company/ai-agents/runs/{run.id}/edit",
                       data={"csrf_token": CSRF, "report": "hijack"}).status_code == 404
    run = step_app.db.session.get(AgentRun, run.id)
    assert run.is_saved is False and run.edited_report is None


def test_other_company_history_is_empty(app, client, make_user):
    _company_a_run(client, make_user)
    login(client, make_user(email="b@corp.com"))
    html = client.get("/company/ai-agents/runs").get_data(as_text=True)
    assert "Around 40%" not in html and "No agent runs match" in html
    assert "Around 40%" not in client.get("/company/ai-agents").get_data(as_text=True)


def test_other_company_cannot_attach_foreign_files(app, client, make_user):
    _company_a_run(client, make_user)
    file_id = AgentFile.query.one().id
    login(client, make_user(email="b@corp.com"))
    client.post("/company/ai-agents/business-analyst", data={**ba_form(), "reuse_file_ids": [str(file_id)]})
    b_run = AgentRun.query.order_by(AgentRun.id.desc()).first()
    assert b_run.files == []
    assert "Confidential A roadmap" not in app.fake_provider.calls[-1]["prompt"]


def test_other_company_cannot_prefill_or_link_task_from_foreign_run(app, client, make_user):
    _, run = _company_a_run(client, make_user)
    b = make_user(email="b@corp.com")
    login(client, b)

    html = client.get(f"/tasks/new?from_run={run.id}").get_data(as_text=True)
    assert "User research and redesign proposal" not in html

    client.post("/tasks/new", data={"title": "B task", "requirements": "", "estimated_hours": "",
                                    "source_run_id": str(run.id)})
    task = step_app.Task.query.one()
    assert task.company_id == b.id
    assert step_app.db.session.get(AgentRun, run.id).created_task_id is None


def test_existing_applicant_routes_are_owner_only(app, client, make_user):
    # Pre-existing IDOR fixed as part of company isolation
    a, b, student = make_user(email="a@corp.com"), make_user(email="b@corp.com"), make_user("student")
    task = step_app.Task(title="A's task", company_id=a.id)
    step_app.db.session.add(task)
    step_app.db.session.commit()
    application = step_app.Application(task_id=task.id, student_id=student.id)
    step_app.db.session.add(application)
    step_app.db.session.commit()

    login(client, b)
    assert client.get(f"/company/task/{task.id}/applicants").status_code == 404
    assert client.get(f"/company/application/{application.id}/select").status_code == 404
    assert step_app.db.session.get(step_app.Application, application.id).status == "pending"

    client.get("/logout")
    login(client, a)
    assert client.get(f"/company/task/{task.id}/applicants").status_code == 200
    assert client.get(f"/company/application/{application.id}/select").status_code == 302
    assert step_app.db.session.get(step_app.Application, application.id).status == "accepted"
