# Agent execution service: authorisation, input validation, execution and
# history. Routes stay thin; everything that touches company data goes through
# the helpers here so company isolation is enforced in one place.

import logging
import os
import socket
import threading
import time
from datetime import timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from flask import abort, current_app
from pydantic import BaseModel, ValidationError
from sqlalchemy import update

from extensions import db

from .models import AgentFile, AgentRun, AIAgent, AIAgentAccess, utcnow
from .providers import ProviderError, Usage, get_provider
from .registry import AgentContext, BaseAgent, DocumentContext, all_agents, get_agent

log = logging.getLogger(__name__)


class AgentUnavailable(Exception):
    pass


class InputInvalid(Exception):
    def __init__(self, errors: Dict[str, str]):
        super().__init__("invalid input")
        self.errors = errors


class RunLimitReached(Exception):
    pass


# ---------------------------------------------------------------------------
# Catalogue and access
# ---------------------------------------------------------------------------

def sync_agent_records() -> Dict[str, AIAgent]:
    """Ensure every registered agent has a catalogue row; refresh metadata from code.

    status and access_scope are owned by the database and never overwritten here.
    """
    records = {r.slug: r for r in AIAgent.query.all()}
    changed = False
    for agent in all_agents():
        record = records.get(agent.slug)
        if record is None:
            record = AIAgent(slug=agent.slug, status="active", access_scope="all")
            db.session.add(record)
            records[agent.slug] = record
            changed = True
        meta = {
            "name": agent.name,
            "description": agent.description,
            "category": agent.category,
            "icon": agent.icon,
            "version": agent.version,
        }
        for key, value in meta.items():
            if getattr(record, key) != value:
                setattr(record, key, value)
                changed = True
    if changed:
        db.session.commit()
    return records


def is_available_to(record: AIAgent, company_id: int) -> bool:
    if record is None or record.status != "active":
        return False
    agent = get_agent(record.slug)
    if agent is not None and agent.requires_web_search and not current_app.config.get("AI_WEB_SEARCH_ENABLED"):
        return False
    if record.access_scope == "all":
        return True
    if record.access_scope == "restricted":
        return AIAgentAccess.query.filter_by(agent_id=record.id, company_id=company_id).first() is not None
    return False


def agents_for_company(company_id: int) -> List[Tuple[BaseAgent, AIAgent]]:
    records = sync_agent_records()
    return [(a, records[a.slug]) for a in all_agents() if is_available_to(records.get(a.slug), company_id)]


def resolve_agent(slug: str, company_id: int) -> Tuple[BaseAgent, AIAgent]:
    agent = get_agent(slug)
    if agent is None:
        raise AgentUnavailable(slug)
    record = sync_agent_records().get(slug)
    if not is_available_to(record, company_id):
        raise AgentUnavailable(slug)
    return agent, record


# ---------------------------------------------------------------------------
# Company-scoped lookups (every read of runs/files must use these)
# ---------------------------------------------------------------------------

def company_run_or_404(run_id: int, company_id: int) -> AgentRun:
    # 404 rather than 403 so other companies cannot probe which run ids exist
    run = AgentRun.query.filter_by(id=run_id, company_id=company_id).first()
    if run is None:
        abort(404)
    return run


def company_file_or_404(file_id: int, company_id: int) -> AgentFile:
    f = AgentFile.query.filter_by(id=file_id, company_id=company_id).first()
    if f is None:
        abort(404)
    return f


def company_files(file_ids: Iterable, company_id: int) -> List[AgentFile]:
    ids = set()
    for raw in file_ids:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    return AgentFile.query.filter(AgentFile.id.in_(ids), AgentFile.company_id == company_id).all()


# ---------------------------------------------------------------------------
# Validation and limits
# ---------------------------------------------------------------------------

def validate_input(agent: BaseAgent, raw: dict) -> BaseModel:
    allowed = {f.name for f in agent.input_fields}
    cleaned = {k: v for k, v in raw.items() if k in allowed}
    try:
        return agent.input_model.model_validate(cleaned)
    except ValidationError as exc:
        labels = {f.name: f.label for f in agent.input_fields}
        errors = {}
        for err in exc.errors():
            field = str(err["loc"][0]) if err.get("loc") else "__all__"
            errors.setdefault(field, f"{labels.get(field, field)}: {err['msg']}")
        raise InputInvalid(errors) from exc


def check_run_limit(company_id: int) -> None:
    limit = int(current_app.config["AI_MAX_RUNS_PER_DAY"])
    if limit <= 0:
        return
    since = utcnow() - timedelta(days=1)
    count = AgentRun.query.filter(AgentRun.company_id == company_id, AgentRun.created_at >= since).count()
    if count >= limit:
        raise RunLimitReached(limit)


def build_documents(files: List[AgentFile]) -> List[DocumentContext]:
    budget = int(current_app.config["AI_MAX_TOTAL_DOCUMENT_CHARS"])
    docs = []
    for f in files:
        text = f.extracted_text or ""
        truncated = bool(f.truncated)
        if budget <= 0:
            text, truncated = "", True
        elif len(text) > budget:
            text, truncated = text[:budget], True
        budget -= len(text)
        docs.append(DocumentContext(filename=f.original_filename, text=text, truncated=truncated))
    return docs


# ---------------------------------------------------------------------------
# Execution: runs are queued, then claimed and executed by one of
#   inline - in the request (tests, debugging)
#   thread - a background thread in the web process (default; no extra process)
#   worker - a separate `flask ai-agents worker` process polling the queue (production)
# Claiming is an atomic UPDATE, so a run is never executed twice even with
# several web processes and workers.
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = ("succeeded", "failed")


def estimate_cost(model: Optional[str], usage: Usage) -> Optional[Decimal]:
    prices = current_app.config["AI_MODEL_PRICES"].get(model or "")
    if not prices:
        return None
    in_price, out_price = prices
    cost = (Decimal(usage.input_tokens) * Decimal(str(in_price))
            + Decimal(usage.output_tokens) * Decimal(str(out_price))) / Decimal(1_000_000)
    if usage.web_search_requests:
        per_1k = Decimal(str(current_app.config["AI_WEB_SEARCH_PRICE_PER_1K"]))
        cost += Decimal(usage.web_search_requests) * per_1k / Decimal(1000)
    return cost.quantize(Decimal("0.000001"))


def submit_run(*, agent: BaseAgent, record: AIAgent, data: BaseModel, files: List[AgentFile],
               company_id: int, user_id: int, parent_run_id: Optional[int] = None,
               refinement: Optional[str] = None, context_run_id: Optional[int] = None) -> AgentRun:
    """Queue a run and hand it to the configured executor. Returns immediately except in inline mode."""
    run = AgentRun(
        company_id=company_id,
        user_id=user_id,
        agent_id=record.id,
        agent_slug=agent.slug,
        agent_version=agent.version,
        status="queued",
        title=(f"Revised: {agent.run_title(data)}" if refinement else agent.run_title(data))[:200],
        input_data=data.model_dump(mode="json"),
        parent_run_id=parent_run_id,
        refinement=refinement,
        context_run_id=context_run_id,
    )
    run.files = list(files)
    db.session.add(run)
    db.session.commit()
    dispatch(run.id)
    return run


def dispatch(run_id: int):
    mode = current_app.config["AI_EXECUTION_MODE"]
    if mode == "inline":
        process_run(run_id, worker_id="inline")
        db.session.expire_all()
        return None
    if mode == "thread":
        app = current_app._get_current_object()
        thread = threading.Thread(target=_run_in_thread, args=(app, run_id), name=f"ai-run-{run_id}", daemon=True)
        thread.start()
        return thread
    # "worker": a `flask ai-agents worker` process picks it up from the queue
    return None


def _run_in_thread(app, run_id: int) -> None:
    with app.app_context():
        try:
            process_run(run_id, worker_id=f"thread:{socket.gethostname()}:{os.getpid()}")
        finally:
            db.session.remove()


def claim_run(run_id: int, worker_id: str) -> bool:
    """Atomically move a run from queued to running. False if someone else claimed it."""
    result = db.session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "queued")
        .values(status="running", started_at=utcnow(), worker_id=worker_id[:100])
    )
    db.session.commit()
    return result.rowcount == 1


def claim_next(worker_id: str) -> Optional[int]:
    """Claim the oldest queued run, retrying if another worker wins the race."""
    for _ in range(5):
        run_id = (db.session.query(AgentRun.id).filter(AgentRun.status == "queued")
                  .order_by(AgentRun.created_at, AgentRun.id).limit(1).scalar())
        if run_id is None:
            return None
        if claim_run(run_id, worker_id):
            return run_id
    return None


def process_run(run_id: int, worker_id: str) -> bool:
    """Claim and execute one run. Returns False if it was already claimed."""
    if not claim_run(run_id, worker_id):
        return False
    execute_claimed_run(run_id)
    return True


def execute_claimed_run(run_id: int) -> None:
    """Execute a run already in 'running' state and persist the outcome. Failures are stored, not raised."""
    run = db.session.get(AgentRun, run_id)
    agent = get_agent(run.agent_slug)
    started = time.monotonic()
    ctx = None
    try:
        if agent is None or not is_available_to(run.agent, run.company_id):
            raise ProviderError("agent_unavailable", "This agent is no longer available to your company.")
        data = agent.input_model.model_validate(run.input_data)
        provider = get_provider()
        run.provider = provider.name
        documents = context_documents(run) + build_documents(list(run.files))
        previous = None
        if run.refinement and run.parent_run_id:
            parent = AgentRun.query.filter_by(id=run.parent_run_id, company_id=run.company_id).first()
            previous = parent.output_data if parent is not None else None
            if previous is None:
                raise ProviderError("refinement_unavailable", "The result you asked to revise is no longer available.")
        ctx = AgentContext(data=data, documents=documents, files=list(run.files), provider=provider,
                           config=current_app.config, refinement=run.refinement, previous_output=previous)
        result = agent.execute(ctx)
        run.output_data = result.output.model_dump(mode="json")
        run.artifacts = result.artifacts
        run.status = "succeeded"
    except ProviderError as exc:
        log.warning("Agent run %s failed (%s): %s", run.id, exc.code, exc)
        run.status = "failed"
        run.error_code = exc.code
        run.error_message = exc.user_message
        if ctx is not None and exc.usage:
            ctx.usage = ctx.usage + exc.usage
    except Exception:  # noqa: BLE001 - never leave a run stuck in "running"
        log.exception("Unexpected error in agent run %s", run.id)
        db.session.rollback()
        run = db.session.get(AgentRun, run_id)
        run.status = "failed"
        run.error_code = "internal_error"
        run.error_message = "Something went wrong while running the agent. The error has been logged for the STEP team."
    finally:
        if ctx is not None:
            _record_usage(run, ctx)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = utcnow()
        db.session.commit()
    _notify_finished(run, agent)


def _notify_finished(run: AgentRun, agent: Optional[BaseAgent]) -> None:
    # Background runs tell the company when they finish; inline runs show the result directly
    if current_app.config.get("AI_EXECUTION_MODE") == "inline":
        return
    from flask import url_for

    from core.notifications import notify

    try:
        name = agent.name if agent else run.agent_slug
        with current_app.test_request_context():
            link = url_for("ai_agents.run_detail", run_id=run.id)
        if run.status == "succeeded":
            notify(run.company_id, f"Your {name} result is ready: \"{run.title}\".", link)
        else:
            notify(run.company_id, f"Your {name} run \"{run.title}\" did not complete.", link)
        db.session.commit()
    except Exception:  # noqa: BLE001 - a notification must never break the worker
        db.session.rollback()
        log.exception("Could not create completion notification for run %s", run.id)


def context_documents(run: AgentRun) -> List[DocumentContext]:
    """The earlier result passed in via "Continue in another agent" (same company only)."""
    if not run.context_run_id:
        return []
    source = AgentRun.query.filter_by(id=run.context_run_id, company_id=run.company_id, status="succeeded").first()
    if source is None:
        return []
    text = report_text(source)
    if not text:
        return []
    source_agent = get_agent(source.agent_slug)
    label = source_agent.name if source_agent else source.agent_slug
    return [DocumentContext(filename=f"{label} result (run {source.id}).md", text=text, truncated=False)]


def report_text(run: AgentRun) -> Optional[str]:
    """The company-facing report for a run: their edited version if any, else the AI result as Markdown."""
    if run.edited_report:
        return run.edited_report
    agent = get_agent(run.agent_slug)
    output = parsed_output(agent, run) if agent else None
    return agent.to_markdown(output, artifacts=run.artifacts) if output is not None else None


def _record_usage(run: AgentRun, ctx: AgentContext) -> None:
    usage = ctx.usage
    run.model = ctx.model or run.model
    if not (usage.input_tokens or usage.output_tokens or usage.web_search_requests):
        return
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.web_search_requests = usage.web_search_requests or None
    run.cost_usd = estimate_cost(run.model or current_app.config.get("AI_MODEL"), usage)


# Messages for runs that never reached a terminal state
_STALE_RUNNING = ("interrupted", "This run was interrupted before it finished (the server may have restarted). Please run it again.")
_STALE_QUEUED = ("not_started", "This run was not started because no background worker picked it up. Please run it again, or ask the STEP administrator to check the AI worker.")


def _stale_cutoffs():
    now = utcnow()
    return (now - timedelta(seconds=int(current_app.config["AI_STALE_RUN_SECONDS"])),
            now - timedelta(seconds=int(current_app.config["AI_QUEUE_TIMEOUT_SECONDS"])))


def expire_if_stale(run: AgentRun) -> AgentRun:
    """Mark a single run failed if it has been running or queued for too long."""
    running_cutoff, queued_cutoff = _stale_cutoffs()
    if run.status == "running" and run.started_at and run.started_at < running_cutoff:
        _fail(run, *_STALE_RUNNING)
    elif run.status == "queued" and run.created_at < queued_cutoff:
        _fail(run, *_STALE_QUEUED)
    return run


def reap_stale_runs() -> int:
    running_cutoff, queued_cutoff = _stale_cutoffs()
    stale = AgentRun.query.filter(
        db.or_(
            db.and_(AgentRun.status == "running", AgentRun.started_at < running_cutoff),
            db.and_(AgentRun.status == "queued", AgentRun.created_at < queued_cutoff),
        )
    ).all()
    for run in stale:
        expire_if_stale(run)
    return len(stale)


def _fail(run: AgentRun, code: str, message: str) -> None:
    # Conditional update so a run that finished meanwhile is left alone
    result = db.session.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == run.status)
        .values(status="failed", error_code=code, error_message=message, completed_at=utcnow())
    )
    db.session.commit()
    if result.rowcount:
        db.session.refresh(run)


def run_worker(*, worker_id: str, once: bool = False, poll_interval: float = 2.0, should_stop=lambda: False) -> int:
    """Worker loop for `flask ai-agents worker`. Returns the number of runs processed."""
    processed = 0
    while not should_stop():
        reap_stale_runs()
        run_id = claim_next(worker_id)
        if run_id is not None:
            log.info("Worker %s executing run %s", worker_id, run_id)
            execute_claimed_run(run_id)
            processed += 1
            db.session.remove()
            continue
        db.session.remove()
        if once:
            break
        time.sleep(poll_interval)
    return processed


def parsed_output(agent: BaseAgent, run: AgentRun) -> Optional[BaseModel]:
    if run.status != "succeeded" or not run.output_data:
        return None
    try:
        return agent.output_model.model_validate(run.output_data)
    except ValidationError:
        # Output from an older agent version whose schema has since changed
        log.warning("Stored output for run %s no longer matches %s v%s", run.id, agent.slug, agent.version)
        return None
