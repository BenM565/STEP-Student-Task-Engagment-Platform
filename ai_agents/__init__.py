# AI Agents for STEP.
# init_ai_agents(app, task_model=Task) wires configuration, models, the agent
# registry, routes and CLI commands into the existing Flask app.

import os

import click

from extensions import db

from . import agents  # noqa: F401  (registers built-in agents)
from . import models  # noqa: F401  (registers tables with SQLAlchemy)
from .registry import all_agents
from .security import csrf_token
from .task_bridge import StepTaskDraft, draft_to_task_form

# Per-million-token prices (USD) used for cost estimates in run history.
# Source: Anthropic API pricing, checked September 2026. Override for the active
# model with AI_INPUT_PRICE_PER_MTOK / AI_OUTPUT_PRICE_PER_MTOK when prices change.
DEFAULT_MODEL_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _configure(app) -> None:
    env = os.environ
    defaults = {
        "AI_PROVIDER": env.get("AI_PROVIDER", "anthropic"),
        "ANTHROPIC_API_KEY": env.get("ANTHROPIC_API_KEY"),
        "AI_MODEL": env.get("AI_MODEL", "claude-opus-5-5"),
        # low | medium | high | xhigh | max
        "AI_EFFORT": env.get("AI_EFFORT", "high"),
        "AI_TIMEOUT_SECONDS": float(env.get("AI_TIMEOUT_SECONDS", "300")),
        "AI_MAX_RUNS_PER_DAY": int(env.get("AI_MAX_RUNS_PER_DAY", "50")),
        "AI_MAX_FILE_BYTES": int(env.get("AI_MAX_FILE_MB", "10")) * 1024 * 1024,
        "AI_MAX_DOCUMENT_CHARS": int(env.get("AI_MAX_DOCUMENT_CHARS", "100000")),
        "AI_MAX_TOTAL_DOCUMENT_CHARS": int(env.get("AI_MAX_TOTAL_DOCUMENT_CHARS", "200000")),
        "AI_UPLOAD_DIR": env.get("AI_UPLOAD_DIR") or os.path.join(app.instance_path, "ai_uploads"),
        # inline | thread | worker (see service.py)
        "AI_EXECUTION_MODE": env.get("AI_EXECUTION_MODE", "thread"),
        # A run still "running" after this long is treated as interrupted
        "AI_STALE_RUN_SECONDS": int(env.get("AI_STALE_RUN_SECONDS", "1800")),
        # A run still "queued" after this long is failed (no worker running)
        "AI_QUEUE_TIMEOUT_SECONDS": int(env.get("AI_QUEUE_TIMEOUT_SECONDS", "3600")),
        # Live web research (Market Research agent); also needs web search enabled for the Anthropic org
        "AI_WEB_SEARCH_ENABLED": env.get("AI_WEB_SEARCH_ENABLED", "1").lower() in ("1", "true", "yes"),
        "AI_WEB_SEARCH_MAX_USES": int(env.get("AI_WEB_SEARCH_MAX_USES", "8")),
        # USD per 1,000 searches (Anthropic list price, checked September 2026)
        "AI_WEB_SEARCH_PRICE_PER_1K": float(env.get("AI_WEB_SEARCH_PRICE_PER_1K", "10")),
        # Data Analysis agent limits
        "AI_DATA_MAX_ROWS": int(env.get("AI_DATA_MAX_ROWS", "100000")),
        "AI_DATA_MAX_COLUMNS": int(env.get("AI_DATA_MAX_COLUMNS", "100")),
    }
    for key, value in defaults.items():
        app.config.setdefault(key, value)

    prices = dict(DEFAULT_MODEL_PRICES)
    if env.get("AI_INPUT_PRICE_PER_MTOK") and env.get("AI_OUTPUT_PRICE_PER_MTOK"):
        prices[app.config["AI_MODEL"]] = (float(env["AI_INPUT_PRICE_PER_MTOK"]), float(env["AI_OUTPUT_PRICE_PER_MTOK"]))
    app.config.setdefault("AI_MODEL_PRICES", prices)

    # Request body cap: up to 5 documents plus form fields. Flask ships the key as
    # None, so setdefault would be a no-op; only respect an explicit existing cap.
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = app.config["AI_MAX_FILE_BYTES"] * 5 + 1024 * 1024

    if app.config["AI_EXECUTION_MODE"] not in ("inline", "thread", "worker"):
        raise ValueError("AI_EXECUTION_MODE must be inline, thread or worker")


def init_ai_agents(app, *, task_model) -> None:
    _configure(app)
    app.extensions["ai_agents"] = {"task_model": task_model}

    from .routes import bp

    app.register_blueprint(bp)
    app.jinja_env.globals["ai_csrf_token"] = csrf_token
    _register_cli(app)


def _register_cli(app) -> None:
    from .models import AIAgentAccess
    from .schema import ensure_schema
    from .service import sync_agent_records

    group = click.Group("ai-agents", help="Manage STEP AI agents.")

    @group.command("sync")
    def sync_cmd():
        """Create/upgrade AI tables and sync the agent catalogue from code."""
        for column in ensure_schema():
            click.echo(f"added column {column}")
        for slug, record in sorted(sync_agent_records().items()):
            click.echo(f"{slug:24} v{record.version:8} {record.status:9} access={record.access_scope}")

    @group.command("set-status")
    @click.argument("slug")
    @click.argument("status", type=click.Choice(["active", "disabled"]))
    def set_status(slug, status):
        """Enable or disable an agent for all companies."""
        record = sync_agent_records().get(slug)
        if record is None:
            raise click.ClickException(f"Unknown agent '{slug}'")
        record.status = status
        db.session.commit()
        click.echo(f"{slug} is now {status}")

    @group.command("set-access")
    @click.argument("slug")
    @click.argument("scope", type=click.Choice(["all", "restricted"]))
    def set_access(slug, scope):
        """Make an agent available to all companies or only allowlisted ones."""
        record = sync_agent_records().get(slug)
        if record is None:
            raise click.ClickException(f"Unknown agent '{slug}'")
        record.access_scope = scope
        db.session.commit()
        click.echo(f"{slug} access is now {scope}")

    @group.command("grant")
    @click.argument("slug")
    @click.argument("company_id", type=int)
    def grant(slug, company_id):
        """Allow a company to use a restricted agent."""
        record = sync_agent_records().get(slug)
        if record is None:
            raise click.ClickException(f"Unknown agent '{slug}'")
        if not AIAgentAccess.query.filter_by(agent_id=record.id, company_id=company_id).first():
            db.session.add(AIAgentAccess(agent_id=record.id, company_id=company_id))
            db.session.commit()
        click.echo(f"Company {company_id} can use {slug}")

    @group.command("worker")
    @click.option("--once", is_flag=True, help="Process queued runs, then exit when the queue is empty.")
    @click.option("--poll-interval", default=2.0, show_default=True, help="Seconds between queue checks when idle.")
    def worker(once, poll_interval):
        """Execute queued agent runs (use with AI_EXECUTION_MODE=worker). Run several for concurrency."""
        import signal
        import socket

        from .service import run_worker

        stop = {"requested": False}

        def request_stop(signum, frame):
            # Finish the current run, then exit
            stop["requested"] = True
            click.echo("Stopping after the current run…")

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        worker_id = f"worker:{socket.gethostname()}:{os.getpid()}"
        click.echo(f"{worker_id} polling for agent runs")
        count = run_worker(worker_id=worker_id, once=once, poll_interval=poll_interval,
                           should_stop=lambda: stop["requested"])
        click.echo(f"{worker_id} processed {count} run(s)")

    @group.command("live-check")
    @click.argument("slug", default="business-analyst")
    def live_check(slug):
        """Make one real call to the AI provider with a sample input (nothing is saved)."""
        from .providers import ProviderError, get_provider
        from .registry import AgentContext, get_agent
        from .service import estimate_cost

        agent = get_agent(slug)
        if agent is None or agent.sample_input is None:
            usable = ", ".join(a.slug for a in all_agents() if a.sample_input is not None)
            raise click.ClickException(f"live-check supports: {usable}")
        try:
            ctx = AgentContext(data=agent.input_model.model_validate(agent.sample_input), documents=[], files=[],
                               provider=get_provider(), config=app.config)
            result = agent.execute(ctx)
        except ProviderError as exc:
            raise click.ClickException(f"{exc.code}: {exc.user_message}\n  detail: {exc}")
        cost = estimate_cost(ctx.model, ctx.usage)
        click.echo(f"OK  agent={slug} model={ctx.model}")
        click.echo(f"    tokens in={ctx.usage.input_tokens:,} out={ctx.usage.output_tokens:,} "
                   f"searches={ctx.usage.web_search_requests} est_cost=${cost}")
        click.echo(agent.to_markdown(result.output)[:1500])

    app.cli.add_command(group)


__all__ = ["init_ai_agents", "StepTaskDraft", "draft_to_task_form"]
