# Background execution: queueing, atomic claiming, worker, thread mode,
# status polling, stale-run handling and the additive schema upgrade.

from datetime import timedelta

from conftest import ba_form, login, step_app
from sqlalchemy import inspect, text

from ai_agents import service
from ai_agents.models import AgentRun, utcnow
from ai_agents.schema import ensure_schema


def _queue_one(app, client, make_user):
    app.config["AI_EXECUTION_MODE"] = "worker"
    login(client, make_user())
    resp = client.post("/company/ai-agents/business-analyst", data=ba_form())
    return resp, AgentRun.query.one()


def test_worker_mode_queues_without_executing(app, client, make_user):
    resp, run = _queue_one(app, client, make_user)
    assert resp.status_code == 302
    assert run.status == "queued" and app.fake_provider.calls == []
    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    assert "Waiting to start" in html and "runProgress" in html
    assert client.get(f"/company/ai-agents/runs/{run.id}/status").get_json() == {"id": run.id, "status": "queued", "done": False}


def test_worker_command_processes_queue(app, client, make_user):
    _, run = _queue_one(app, client, make_user)
    run_id = run.id  # the worker clears the session between runs
    result = app.test_cli_runner().invoke(args=["ai-agents", "worker", "--once"])
    assert result.exit_code == 0, result.output
    assert "processed 1 run(s)" in result.output
    run = step_app.db.session.get(AgentRun, run_id)
    assert run.status == "succeeded" and run.worker_id.startswith("worker:") and run.started_at is not None
    assert run.output_data["summary"] and run.input_tokens == 1000


def test_a_run_can_only_be_claimed_once(app, client, make_user):
    _, run = _queue_one(app, client, make_user)
    assert service.claim_run(run.id, "worker-a") is True
    assert service.claim_run(run.id, "worker-b") is False
    assert service.claim_next("worker-c") is None
    assert step_app.db.session.get(AgentRun, run.id).worker_id == "worker-a"


def test_claim_next_takes_oldest_first(app, client, make_user):
    app.config["AI_EXECUTION_MODE"] = "worker"
    login(client, make_user())
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    client.post("/company/ai-agents/business-analyst", data=ba_form(problem="Second problem description goes here"))
    first, second = AgentRun.query.order_by(AgentRun.id).all()
    assert service.claim_next("w") == first.id
    assert service.claim_next("w") == second.id


def test_thread_mode_runs_in_background(app, client, make_user):
    app.config["AI_EXECUTION_MODE"] = "thread"
    started = []
    original = service.dispatch

    def capture(run_id):
        thread = original(run_id)
        started.append(thread)
        return thread

    service.dispatch = capture
    try:
        login(client, make_user())
        client.post("/company/ai-agents/business-analyst", data=ba_form())
    finally:
        service.dispatch = original
    started[0].join(timeout=10)
    step_app.db.session.expire_all()
    run = AgentRun.query.one()
    assert run.status == "succeeded" and run.worker_id.startswith("thread:")


def test_stale_runs_are_failed_not_left_hanging(app, client, make_user):
    _, queued = _queue_one(app, client, make_user)
    client.post("/company/ai-agents/business-analyst", data=ba_form())
    running = AgentRun.query.order_by(AgentRun.id.desc()).first()
    service.claim_run(running.id, "dead-worker")

    long_ago = (utcnow() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S.%f")
    step_app.db.session.execute(text("UPDATE ai_agent_runs SET created_at = :t"), {"t": long_ago})
    step_app.db.session.execute(text("UPDATE ai_agent_runs SET started_at = :t WHERE id = :id"), {"t": long_ago, "id": running.id})
    step_app.db.session.commit()

    assert service.reap_stale_runs() == 2
    step_app.db.session.expire_all()
    assert step_app.db.session.get(AgentRun, queued.id).error_code == "not_started"
    assert step_app.db.session.get(AgentRun, running.id).error_code == "interrupted"
    assert "Run again" in client.get(f"/company/ai-agents/runs/{running.id}").get_data(as_text=True)


def test_status_endpoint_is_company_scoped(app, client, make_user):
    _, run = _queue_one(app, client, make_user)
    client.get("/logout")
    login(client, make_user(email="other@corp.com"))
    assert client.get(f"/company/ai-agents/runs/{run.id}/status").status_code == 404


def test_disabling_agent_after_queueing_fails_run_cleanly(app, client, make_user):
    from ai_agents.models import AIAgent

    _, run = _queue_one(app, client, make_user)
    AIAgent.query.filter_by(slug="business-analyst").one().status = "disabled"
    step_app.db.session.commit()
    service.process_run(run.id, "w")
    step_app.db.session.expire_all()
    run = step_app.db.session.get(AgentRun, run.id)
    assert run.status == "failed" and run.error_code == "agent_unavailable"


def test_ensure_schema_adds_columns_to_existing_table(app):
    # Simulate a database created by the first AI Agents release
    step_app.db.session.execute(text("DROP TABLE ai_agent_run_files"))
    step_app.db.session.execute(text("DROP TABLE ai_agent_runs"))
    step_app.db.session.execute(text("""
        CREATE TABLE ai_agent_runs (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, user_id INTEGER NOT NULL, agent_id INTEGER NOT NULL,
            agent_slug VARCHAR(64) NOT NULL, agent_version VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL,
            title VARCHAR(200), input_data JSON NOT NULL, output_data JSON, edited_report TEXT, error_code VARCHAR(64),
            error_message TEXT, provider VARCHAR(32), model VARCHAR(64), input_tokens INTEGER, output_tokens INTEGER,
            cost_usd NUMERIC(10, 6), duration_ms INTEGER, is_saved BOOLEAN NOT NULL, created_task_id INTEGER,
            parent_run_id INTEGER, created_at DATETIME NOT NULL, completed_at DATETIME)"""))
    step_app.db.session.commit()

    added = ensure_schema()
    assert set(added) == {"ai_agent_runs.artifacts", "ai_agent_runs.web_search_requests",
                          "ai_agent_runs.started_at", "ai_agent_runs.worker_id",
                          "ai_agent_runs.refinement", "ai_agent_runs.context_run_id"}
    columns = {c["name"] for c in inspect(step_app.db.engine).get_columns("ai_agent_runs")}
    assert {"artifacts", "started_at", "worker_id", "web_search_requests"} <= columns
    assert "ai_agent_run_files" in inspect(step_app.db.engine).get_table_names()
    assert ensure_schema() == []  # idempotent


def test_sync_command_reports_agents(app):
    result = app.test_cli_runner().invoke(args=["ai-agents", "sync"])
    assert result.exit_code == 0, result.output
    for slug in ("business-analyst", "document-analysis", "market-research", "data-analysis", "requirements-to-task"):
        assert slug in result.output


def test_live_check_command(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["ai-agents", "live-check", "business-analyst"])
    assert result.exit_code == 0, result.output
    assert "OK  agent=business-analyst model=claude-opus-5-5" in result.output
    assert "tokens in=1,000 out=500" in result.output and "# Business analysis" in result.output
    assert AgentRun.query.count() == 0  # nothing persisted

    bad = runner.invoke(args=["ai-agents", "live-check", "data-analysis"])
    assert bad.exit_code != 0 and "live-check supports" in bad.output


def test_live_check_reports_provider_errors(app):
    app.config["AI_PROVIDER_INSTANCE"] = None  # real provider, no API key
    result = app.test_cli_runner().invoke(args=["ai-agents", "live-check"])
    assert result.exit_code != 0 and "provider_not_configured" in result.output
