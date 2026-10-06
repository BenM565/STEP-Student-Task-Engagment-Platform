# Agent execution service: authorisation, input validation, execution and
# history. Routes stay thin; everything that touches company data goes through
# the helpers here so company isolation is enforced in one place.

import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from flask import abort, current_app
from pydantic import BaseModel, ValidationError

from extensions import db

from .models import AgentFile, AgentRun, AIAgent, AIAgentAccess, utcnow
from .providers import ProviderError, get_provider
from .registry import BaseAgent, DocumentContext, all_agents, get_agent

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
# Execution
# ---------------------------------------------------------------------------

def estimate_cost(model: Optional[str], input_tokens: int, output_tokens: int) -> Optional[Decimal]:
    prices = current_app.config["AI_MODEL_PRICES"].get(model or "")
    if not prices:
        return None
    in_price, out_price = prices
    cost = (Decimal(input_tokens) * Decimal(str(in_price)) + Decimal(output_tokens) * Decimal(str(out_price))) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))


def execute_run(*, agent: BaseAgent, record: AIAgent, data: BaseModel, files: List[AgentFile],
                company_id: int, user_id: int, parent_run_id: Optional[int] = None) -> AgentRun:
    """Run an agent synchronously and persist the outcome. Failures are stored, not raised."""
    run = AgentRun(
        company_id=company_id,
        user_id=user_id,
        agent_id=record.id,
        agent_slug=agent.slug,
        agent_version=agent.version,
        status="running",
        title=agent.run_title(data)[:200],
        input_data=data.model_dump(mode="json"),
        parent_run_id=parent_run_id,
    )
    run.files = list(files)
    db.session.add(run)
    db.session.commit()

    started = time.monotonic()
    try:
        provider = get_provider()
        run.provider = provider.name
        prompt = agent.build_prompt(data, build_documents(files))
        result = provider.structured_output(
            system=agent.full_system_prompt(),
            prompt=prompt,
            output_model=agent.output_model,
            max_tokens=agent.max_output_tokens,
            effort=agent.effort,
        )
        run.output_data = result.output.model_dump(mode="json")
        run.model = result.model
        _record_usage(run, result.usage)
        run.status = "succeeded"
    except ProviderError as exc:
        log.warning("Agent run %s failed (%s): %s", run.id, exc.code, exc)
        run.status = "failed"
        run.error_code = exc.code
        run.error_message = exc.user_message
        if exc.usage:
            _record_usage(run, exc.usage)
    except Exception:  # noqa: BLE001 - never leave a run stuck in "running"
        log.exception("Unexpected error in agent run %s", run.id)
        run.status = "failed"
        run.error_code = "internal_error"
        run.error_message = "Something went wrong while running the agent. The STEP team has been notified in the server logs."
    finally:
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = utcnow()
        db.session.commit()
    return run


def _record_usage(run: AgentRun, usage) -> None:
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.cost_usd = estimate_cost(run.model or current_app.config.get("AI_MODEL"), usage.input_tokens, usage.output_tokens)


def parsed_output(agent: BaseAgent, run: AgentRun) -> Optional[BaseModel]:
    if run.status != "succeeded" or not run.output_data:
        return None
    try:
        return agent.output_model.model_validate(run.output_data)
    except ValidationError:
        # Output from an older agent version whose schema has since changed
        log.warning("Stored output for run %s no longer matches %s v%s", run.id, agent.slug, agent.version)
        return None
