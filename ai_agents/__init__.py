# AI Agents for STEP.
# init_ai_agents(app, task_model=Task) wires configuration, models, the agent
# registry, routes and CLI commands into the existing Flask app.

import os

import click

from extensions import db

from . import agents  # noqa: F401  (registers built-in agents)
from . import models  # noqa: F401  (registers tables with SQLAlchemy)
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


def init_ai_agents(app, *, task_model) -> None:
    _configure(app)
    app.extensions["ai_agents"] = {"task_model": task_model}

    from .routes import bp

    app.register_blueprint(bp)
    app.jinja_env.globals["ai_csrf_token"] = csrf_token
    _register_cli(app)


def _register_cli(app) -> None:
    from .models import AIAgentAccess
    from .service import sync_agent_records

    group = click.Group("ai-agents", help="Manage STEP AI agents.")

    @group.command("sync")
    def sync_cmd():
        """Create AI tables if missing and sync the agent catalogue from code."""
        db.create_all()
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

    app.cli.add_command(group)


__all__ = ["init_ai_agents", "StepTaskDraft", "draft_to_task_form"]
