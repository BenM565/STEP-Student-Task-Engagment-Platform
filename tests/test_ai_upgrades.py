# AI upgrades: refining a result with feedback, continuing a result in another
# agent, the Process Automation Advisor, and completion notifications.

import io
import json

from conftest import CSRF, ba_form, login, step_app

from ai_agents import service
from ai_agents.models import AgentRun
from core.models import Notification

CONTRACT = b"Supplier agreement. Either party may terminate with 90 days notice. The monthly fee is EUR 4,500."


def _run_ba(client, **form):
    client.post("/company/ai-agents/business-analyst", data=ba_form(**form))
    return AgentRun.query.order_by(AgentRun.id.desc()).first()


# --- refine ------------------------------------------------------------------

def test_refine_creates_revision_with_feedback_and_previous_result(app, client, make_user):
    login(client, make_user())
    original = _run_ba(client)
    resp = client.post(f"/company/ai-agents/runs/{original.id}/refine",
                       data={"csrf_token": CSRF, "feedback": "Focus on mobile sign-up only"})
    revised = AgentRun.query.order_by(AgentRun.id.desc()).first()
    assert resp.headers["Location"].endswith(f"/runs/{revised.id}")
    assert revised.id != original.id and revised.status == "succeeded"
    assert revised.parent_run_id == original.id and revised.refinement == "Focus on mobile sign-up only"
    assert revised.title.startswith("Revised: ") and revised.input_data == original.input_data

    prompt = app.fake_provider.calls[-1]["prompt"]
    assert "<previous_result>" in prompt and json.dumps(original.output_data["summary"]) in prompt
    assert '<company_input field="feedback">\nFocus on mobile sign-up only' in prompt
    assert original.output_data is not None  # original kept

    revised_page = client.get(f"/company/ai-agents/runs/{revised.id}").get_data(as_text=True)
    assert "Revision of" in revised_page and "Focus on mobile sign-up only" in revised_page
    original_page = client.get(f"/company/ai-agents/runs/{original.id}").get_data(as_text=True)
    assert f"#{revised.id}" in original_page and "Refine this result" in original_page


def test_refine_validation_and_isolation(app, client, make_user):
    owner = make_user()
    login(client, owner)
    run = _run_ba(client)
    client.post(f"/company/ai-agents/runs/{run.id}/refine", data={"csrf_token": CSRF, "feedback": "no"})
    assert AgentRun.query.count() == 1  # feedback too short
    client.get("/logout")
    login(client, make_user(email="other@corp.com"))
    resp = client.post(f"/company/ai-agents/runs/{run.id}/refine", data={"csrf_token": CSRF, "feedback": "steal this"})
    assert resp.status_code == 404 and AgentRun.query.count() == 1


def test_cannot_refine_failed_run(app, client, make_user):
    from conftest import ProviderError

    login(client, make_user())
    app.fake_provider.error = ProviderError("timeout", "Slow.")
    run = _run_ba(client)
    app.fake_provider.error = None
    client.post(f"/company/ai-agents/runs/{run.id}/refine", data={"csrf_token": CSRF, "feedback": "Please retry"})
    assert AgentRun.query.count() == 1


def test_refine_market_research_steers_new_research(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/market-research", data={
        "csrf_token": CSRF, "idea": "A refill-station shop for cleaning products in Cork city.", "geography": "Cork"})
    run = AgentRun.query.one()
    client.post(f"/company/ai-agents/runs/{run.id}/refine", data={"csrf_token": CSRF, "feedback": "Look at Galway too"})
    research, structured = app.fake_provider.calls[-2:]
    assert research["kind"] == "research" and "Look at Galway too" in research["prompt"]
    assert "<previous_result>" in structured["prompt"]


# --- continue in another agent -----------------------------------------------

def test_continue_result_in_another_agent(app, client, make_user):
    login(client, make_user())
    source = _run_ba(client)
    page = client.get(f"/company/ai-agents/runs/{source.id}").get_data(as_text=True)
    assert f"/company/ai-agents/requirements-to-task?context_run={source.id}" in page
    assert "data-analysis?context_run" not in page  # data agent works from datasets only

    form = client.get(f"/company/ai-agents/requirements-to-task?context_run={source.id}").get_data(as_text=True)
    assert f"run #{source.id}" in form and f'name="context_run" value="{source.id}"' in form

    client.post("/company/ai-agents/requirements-to-task", data={
        "csrf_token": CSRF, "need": "Turn the analysis into a student task", "context_run": str(source.id)})
    target = AgentRun.query.order_by(AgentRun.id.desc()).first()
    assert target.agent_slug == "requirements-to-task" and target.context_run_id == source.id
    prompt = app.fake_provider.calls[-1]["prompt"]
    assert f'filename="Business Analyst result (run {source.id}).md"' in prompt
    assert "# Business analysis" in prompt and "Allow social sign-in" in prompt


def test_context_uses_company_edited_report(app, client, make_user):
    login(client, make_user())
    source = _run_ba(client)
    client.post(f"/company/ai-agents/runs/{source.id}/edit",
                data={"csrf_token": CSRF, "action": "save", "report": "Our edited summary: focus on retention"})
    client.post("/company/ai-agents/requirements-to-task", data={
        "csrf_token": CSRF, "need": "Turn the analysis into a student task", "context_run": str(source.id)})
    assert "Our edited summary: focus on retention" in app.fake_provider.calls[-1]["prompt"]


def test_context_counts_as_document_for_document_analysis(app, client, make_user):
    login(client, make_user())
    source = _run_ba(client)
    resp = client.post("/company/ai-agents/document-analysis",
                       data={"csrf_token": CSRF, "questions": "What are the risks?", "context_run": str(source.id)})
    run = AgentRun.query.order_by(AgentRun.id.desc()).first()
    assert resp.status_code == 302 and run.agent_slug == "document-analysis" and run.status == "succeeded"


def test_cannot_use_another_companys_result_as_context(app, client, make_user):
    login(client, make_user())
    source = _run_ba(client)
    client.get("/logout")
    login(client, make_user(email="other@corp.com"))
    assert client.get(f"/company/ai-agents/requirements-to-task?context_run={source.id}").status_code == 404
    resp = client.post("/company/ai-agents/requirements-to-task", data={
        "csrf_token": CSRF, "need": "Use their analysis please", "context_run": str(source.id)})
    assert resp.status_code == 404
    assert AgentRun.query.count() == 1

    # Even if a context id reached the queue, execution only loads the same company's run
    foreign = AgentRun(company_id=999, user_id=999, agent_id=source.agent_id, agent_slug="requirements-to-task",
                       agent_version="1.0.0", status="queued", input_data={"need": "x" * 20}, context_run_id=source.id)
    assert service.context_documents(foreign) == []


# --- process automation -------------------------------------------------------

def test_process_automation_agent_end_to_end(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/process-automation", data={
        "csrf_token": CSRF, "process": "Invoices arrive by email and an assistant types them into Xero by hand.",
        "volume": "200 a month, 6 minutes each", "tools": "Outlook, Xero"},
        content_type="multipart/form-data")
    run = AgentRun.query.one()
    assert run.status == "succeeded", run.error_message
    prompt = app.fake_provider.calls[0]["prompt"]
    assert "200 a month, 6 minutes each" in prompt and "Outlook, Xero" in prompt
    assert "show their arithmetic" in app.fake_provider.calls[0]["system"]

    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    for text in ("OCR invoice capture", "Keep human", "Approving payments", "Roadmap", "Create STEP task"):
        assert text in html, text
    md = client.get(f"/company/ai-agents/runs/{run.id}/export?format=md").get_data(as_text=True)
    assert "## Opportunities" in md and "16.7 hours/month" in md
    assert 'value="User research and redesign proposal' in client.get(f"/tasks/new?from_run={run.id}").get_data(as_text=True)


def test_process_automation_live_check_sample(app):
    result = app.test_cli_runner().invoke(args=["ai-agents", "live-check", "process-automation"])
    assert result.exit_code == 0 and "# Process automation review" in result.output


# --- notifications -------------------------------------------------------------

def test_background_runs_notify_company(app, client, make_user):
    company = make_user()
    company_id = company.id  # the worker clears the session between runs
    app.config["AI_EXECUTION_MODE"] = "worker"
    login(client, company)
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run_id = AgentRun.query.one().id
    app.test_cli_runner().invoke(args=["ai-agents", "worker", "--once"])
    note = Notification.query.filter_by(user_id=company_id).one()
    assert "Business Analyst result is ready" in note.message and note.link == f"/company/ai-agents/runs/{run_id}"


def test_inline_runs_do_not_notify(app, client, make_user):
    company = make_user()
    login(client, company)
    _run_ba(client)
    assert Notification.query.count() == 0
