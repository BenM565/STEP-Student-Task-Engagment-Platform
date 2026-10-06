# End-to-end company workflow: catalogue -> workspace -> run -> review ->
# save/edit/export -> Create STEP Task -> task exists and is linked to the run.

import json

from conftest import CSRF, ProviderError, ba_form, login, sample_ba_output, step_app

from ai_agents.models import AgentRun, AIAgent, AIAgentAccess
from ai_agents.providers import Usage


def test_unauthenticated_users_are_sent_to_login(client):
    for url in ("/company/ai-agents", "/company/ai-agents/business-analyst", "/company/ai-agents/runs",
                "/company/ai-agents/runs/1", "/company/ai-agents/files/1"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], url


def test_students_cannot_use_agents(client, make_user):
    login(client, make_user("student"))
    resp = client.get("/company/ai-agents")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form())
    assert resp.status_code == 302
    assert AgentRun.query.count() == 0


def test_catalogue_lists_agents(client, make_user):
    login(client, make_user())
    html = client.get("/company/ai-agents").get_data(as_text=True)
    assert "Business Analyst" in html and "Requirements-to-Task" in html
    assert "Use agent" in html


def test_dashboard_links_to_ai_agents_for_companies_only(client, make_user):
    login(client, make_user())
    assert "Open AI Agents" in client.get("/dashboard").get_data(as_text=True)
    client.get("/logout")
    login(client, make_user("student"))
    assert "Open AI Agents" not in client.get("/dashboard").get_data(as_text=True)


def test_successful_run_is_persisted_and_displayed(app, client, make_user):
    company = make_user()
    login(client, company)
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form())
    assert resp.status_code == 302

    run = AgentRun.query.one()
    assert resp.headers["Location"].endswith(f"/company/ai-agents/runs/{run.id}")
    assert run.status == "succeeded"
    assert run.company_id == company.id and run.agent_slug == "business-analyst" and run.agent_version == "1.0.0"
    assert run.input_data["problem"].startswith("Around 40%")
    assert run.output_data["summary"] == "Onboarding loses users at verification."
    assert (run.input_tokens, run.output_tokens) == (1000, 500)
    # claude-opus-5-5 at $4 / $20 per million tokens
    assert float(run.cost_usd) == 0.014
    assert run.duration_ms is not None and run.completed_at is not None

    call = app.fake_provider.calls[0]
    assert "Business Analyst Agent" in call["system"] and "<company_input" in call["system"]
    assert "Around 40% of new customers" in call["prompt"]
    assert call["output_model"].__name__ == "BusinessAnalysisOutput"

    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    for section in ("Problem definition", "Functional requirements", "User stories", "Risks",
                    "Recommended next steps", "Create STEP task", "Allow social sign-in"):
        assert section in html, section


def test_invalid_input_is_rejected_without_running(app, client, make_user):
    login(client, make_user())
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form(problem="too short"))
    assert resp.status_code == 400
    assert "at least 20 characters" in resp.get_data(as_text=True)
    assert AgentRun.query.count() == 0
    assert app.fake_provider.calls == []


def test_unknown_fields_are_not_passed_to_the_agent(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form(company_id="999", system_prompt="evil"))
    assert set(AgentRun.query.one().input_data) == {"problem", "context", "constraints"}


def test_missing_csrf_token_is_rejected(app, client, make_user):
    login(client, make_user())
    data = ba_form()
    data.pop("csrf_token")
    assert client.post("/company/ai-agents/business-analyst", data=data).status_code == 400
    assert client.post("/company/ai-agents/business-analyst", data=ba_form(csrf_token="wrong")).status_code == 400
    assert AgentRun.query.count() == 0


def test_provider_failure_is_recorded_with_safe_message(app, client, make_user):
    login(client, make_user())
    app.fake_provider.error = ProviderError("rate_limited", "The AI service is busy right now.",
                                            detail="429 secret internal detail", retryable=True,
                                            usage=Usage(input_tokens=10, output_tokens=0))
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form(), follow_redirects=True)
    run = AgentRun.query.one()
    assert run.status == "failed" and run.error_code == "rate_limited"
    assert run.input_tokens == 10
    html = resp.get_data(as_text=True)
    assert "The AI service is busy right now." in html
    assert "secret internal detail" not in html
    assert "Run again" in html


def test_unexpected_exception_never_leaves_run_running(app, client, make_user):
    login(client, make_user())
    app.fake_provider.error = RuntimeError("boom")
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run = AgentRun.query.one()
    assert run.status == "failed" and run.error_code == "internal_error"
    assert "boom" not in run.error_message


def test_unconfigured_real_provider_fails_cleanly(app, client, make_user):
    # No injected provider and no ANTHROPIC_API_KEY: the real factory is used
    app.config["AI_PROVIDER_INSTANCE"] = None
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run = AgentRun.query.one()
    assert run.status == "failed" and run.error_code == "provider_not_configured"
    assert "not configured" in run.error_message


def test_disabled_agent_is_hidden_and_not_runnable(app, client, make_user):
    login(client, make_user())
    client.get("/company/ai-agents")  # sync catalogue rows
    AIAgent.query.filter_by(slug="business-analyst").one().status = "disabled"
    step_app.db.session.commit()
    assert "Business Analyst" not in client.get("/company/ai-agents").get_data(as_text=True)
    assert client.get("/company/ai-agents/business-analyst").status_code == 404
    assert client.post("/company/ai-agents/business-analyst", data=ba_form()).status_code == 404
    assert AgentRun.query.count() == 0


def test_restricted_agent_only_for_allowlisted_company(app, client, make_user):
    allowed, other = make_user(), make_user()
    login(client, other)
    client.get("/company/ai-agents")
    record = AIAgent.query.filter_by(slug="business-analyst").one()
    record.access_scope = "restricted"
    step_app.db.session.add(AIAgentAccess(agent_id=record.id, company_id=allowed.id))
    step_app.db.session.commit()

    assert client.get("/company/ai-agents/business-analyst").status_code == 404
    client.get("/logout")
    login(client, allowed)
    assert client.get("/company/ai-agents/business-analyst").status_code == 200


def test_daily_run_limit(app, client, make_user):
    app.config["AI_MAX_RUNS_PER_DAY"] = 1
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form())
    assert resp.status_code == 400 and "limit of 1 agent runs" in resp.get_data(as_text=True)
    assert AgentRun.query.count() == 1


def test_history_lists_runs_with_filters(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    app.fake_provider.error = ProviderError("timeout", "Took too long.")
    client.post("/company/ai-agents/business-analyst", data=ba_form(problem="A second, different business problem here"))

    html = client.get("/company/ai-agents/runs").get_data(as_text=True)
    assert "Around 40% of new customers" in html and "A second, different business problem" in html
    failed_only = client.get("/company/ai-agents/runs?status=failed").get_data(as_text=True)
    assert "A second, different" in failed_only and "Around 40%" not in failed_only


def test_save_edit_and_export(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run = AgentRun.query.one()

    client.post(f"/company/ai-agents/runs/{run.id}/save", data={"csrf_token": CSRF})
    assert AgentRun.query.one().is_saved is True
    assert "Around 40%" in client.get("/company/ai-agents/runs?saved=1").get_data(as_text=True)

    md = client.get(f"/company/ai-agents/runs/{run.id}/export?format=md")
    assert md.status_code == 200 and "attachment" in md.headers["Content-Disposition"]
    assert "## Functional requirements" in md.get_data(as_text=True)

    data = json.loads(client.get(f"/company/ai-agents/runs/{run.id}/export?format=json").get_data(as_text=True))
    assert data["output"]["summary"] == "Onboarding loses users at verification." and data["agent"] == "business-analyst"

    edit_page = client.get(f"/company/ai-agents/runs/{run.id}/edit").get_data(as_text=True)
    assert "# Business analysis" in edit_page
    client.post(f"/company/ai-agents/runs/{run.id}/edit", data={"csrf_token": CSRF, "action": "save", "report": "Edited by us"})
    run = AgentRun.query.one()
    assert run.edited_report == "Edited by us"
    assert run.output_data["summary"] == "Onboarding loses users at verification."  # original kept
    assert client.get(f"/company/ai-agents/runs/{run.id}/export?format=md").get_data(as_text=True) == "Edited by us"

    client.post(f"/company/ai-agents/runs/{run.id}/edit", data={"csrf_token": CSRF, "action": "reset"})
    assert AgentRun.query.one().edited_report is None


def test_run_again_prefills_and_links_parent(app, client, make_user):
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    first = AgentRun.query.one()
    html = client.get(f"/company/ai-agents/business-analyst?rerun={first.id}").get_data(as_text=True)
    assert "Around 40% of new customers" in html and f"run #{first.id}" in html

    client.post(f"/company/ai-agents/business-analyst?rerun={first.id}", data=ba_form())
    second = AgentRun.query.order_by(AgentRun.id.desc()).first()
    assert second.parent_run_id == first.id


def test_create_step_task_from_ai_output(app, client, make_user):
    company = make_user()
    login(client, company)
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run = AgentRun.query.one()

    form_html = client.get(f"/tasks/new?from_run={run.id}").get_data(as_text=True)
    assert "Pre-filled from your AI agent result" in form_html
    assert 'value="User research and redesign proposal for website onboarding"' in form_html
    assert "Acceptance criteria:" in form_html and "At least 6 interviews summarised" in form_html
    assert 'value="40"' in form_html
    assert f'name="source_run_id" value="{run.id}"' in form_html
    # Nothing is created until the company submits the form
    assert step_app.Task.query.count() == 0

    resp = client.post("/tasks/new", data={"title": "Edited onboarding research task", "requirements": "Edited text",
                                           "estimated_hours": "35", "source_run_id": str(run.id)})
    assert resp.status_code == 302
    task = step_app.Task.query.one()
    assert (task.title, task.requirements, task.estimated_hours, task.company_id) == (
        "Edited onboarding research task", "Edited text", 35, company.id)
    assert AgentRun.query.one().created_task_id == task.id

    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    assert "STEP task created" in html


def test_no_task_button_when_ai_does_not_recommend_one(app, client, make_user):
    app.fake_provider.output = sample_ba_output(recommended=False)
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    run = AgentRun.query.one()
    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    assert "No student task recommended yet" in html
    assert f"from_run={run.id}" not in html
    resp = client.get(f"/tasks/new?from_run={run.id}", follow_redirects=True)
    assert "does not include a task to pre-fill" in resp.get_data(as_text=True)


def test_requirements_to_task_agent_end_to_end(app, client, make_user):
    from conftest import sample_task_draft

    app.fake_provider.output = {
        "interpretation": "The company wants fewer drop-offs during sign-up.",
        "suitable_for_student": True,
        "suitability_rationale": "Bounded research and design work.",
        "task": sample_task_draft(estimated_hours=5000),
        "ai_can_handle": ["Summarise existing analytics"],
        "scoping_notes": ["Implementation left for phase 2"],
    }
    login(client, make_user())
    resp = client.post("/company/ai-agents/requirements-to-task",
                       data={"csrf_token": CSRF, "need": "We need to improve our website onboarding process."})
    run = AgentRun.query.one()
    assert resp.status_code == 302 and run.status == "succeeded"
    assert "Proposed STEP task" in client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    # Unrealistic AI estimates are clamped before reaching the task form
    assert 'value="400"' in client.get(f"/tasks/new?from_run={run.id}").get_data(as_text=True)
