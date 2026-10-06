# Company-facing routes for AI agents. Every handler is behind
# guard_company_area and reads runs/files only through company-scoped helpers.

import json

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from extensions import db

from . import service
from .files import FileValidationError, save_uploads, stored_path
from .models import AgentRun
from .registry import all_agents, get_agent
from .security import guard_company_area

bp = Blueprint("ai_agents", __name__, url_prefix="/company/ai-agents")
bp.before_request(guard_company_area)

HISTORY_PAGE_SIZE = 25


@bp.route("")
def catalogue():
    agents = service.agents_for_company(current_user.id)
    recent = (AgentRun.query.filter_by(company_id=current_user.id)
              .order_by(AgentRun.created_at.desc()).limit(5).all())
    return render_template("ai_agents/catalogue.html", agents=agents, recent=recent, agent_names=_agent_names())


@bp.route("/<slug>", methods=["GET", "POST"])
def workspace(slug):
    try:
        agent, record = service.resolve_agent(slug, current_user.id)
    except service.AgentUnavailable:
        abort(404)

    values, errors, prior_files, parent_run_id = {}, {}, [], None

    # "Continue in another agent": an earlier result from this company becomes context
    context_run = None
    context_id = request.values.get("context_run", type=int)
    if context_id and agent.accepts_context:
        context_run = service.company_run_or_404(context_id, current_user.id)
        if context_run.status != "succeeded":
            context_run = None

    rerun_id = request.values.get("rerun", type=int)
    if rerun_id:
        parent = service.company_run_or_404(rerun_id, current_user.id)
        if parent.agent_slug == agent.slug:
            parent_run_id = parent.id
            prior_files = list(parent.files)
            if request.method == "GET":
                values = dict(parent.input_data or {})

    if request.method == "POST":
        values = {f.name: request.form.get(f.name, "") for f in agent.input_fields}
        uploads = [f for f in request.files.getlist("files") if f and f.filename] if agent.accepts_files else []
        reused = service.company_files(request.form.getlist("reuse_file_ids"), current_user.id) if agent.accepts_files else []

        try:
            data = service.validate_input(agent, values)
            attached = len(uploads) + len(reused) + (1 if context_run else 0)
            if attached > agent.max_files:
                noun = "file" if agent.max_files == 1 else "files"
                raise service.InputInvalid({"files": f"Attach at most {agent.max_files} {noun}."})
            if attached < agent.min_files:
                raise service.InputInvalid({"files": f"{agent.files_label}: attach at least {agent.min_files} file to run this agent."})
            service.check_run_limit(current_user.id)
            new_files = save_uploads(uploads, company_id=current_user.id, user_id=current_user.id,
                                     allowed_extensions=agent.allowed_extensions)
        except service.InputInvalid as exc:
            errors = exc.errors
        except FileValidationError as exc:
            errors = {"files": str(exc)}
        except service.RunLimitReached as exc:
            errors = {"__all__": f"Your company has reached its limit of {exc.args[0]} agent runs in 24 hours. Please try again later."}
        else:
            run = service.submit_run(agent=agent, record=record, data=data, files=reused + new_files,
                                     company_id=current_user.id, user_id=current_user.id,
                                     parent_run_id=parent_run_id,
                                     context_run_id=context_run.id if context_run else None)
            return redirect(url_for("ai_agents.run_detail", run_id=run.id))

    status = 400 if errors else 200
    return render_template("ai_agents/workspace.html", agent=agent, values=values, errors=errors,
                           prior_files=prior_files, parent_run_id=parent_run_id, context_run=context_run,
                           agent_names=_agent_names()), status


@bp.route("/runs/<int:run_id>/refine", methods=["POST"])
def refine(run_id):
    """Ask the agent to revise a result using the company's feedback; the original is kept."""
    run = service.company_run_or_404(run_id, current_user.id)
    feedback = (request.form.get("feedback") or "").strip()
    if run.status != "succeeded":
        flash("Only completed results can be refined.", "warning")
        return redirect(url_for("ai_agents.run_detail", run_id=run.id))
    if len(feedback) < 5:
        flash("Tell the agent what to change.", "danger")
        return redirect(url_for("ai_agents.run_detail", run_id=run.id) + "#refine")
    try:
        agent, record = service.resolve_agent(run.agent_slug, current_user.id)
        data = agent.input_model.model_validate(run.input_data)
        service.check_run_limit(current_user.id)
    except service.AgentUnavailable:
        abort(404)
    except service.RunLimitReached as exc:
        flash(f"Your company has reached its limit of {exc.args[0]} agent runs in 24 hours.", "danger")
        return redirect(url_for("ai_agents.run_detail", run_id=run.id))
    new_run = service.submit_run(agent=agent, record=record, data=data, files=list(run.files),
                                 company_id=current_user.id, user_id=current_user.id, parent_run_id=run.id,
                                 refinement=feedback[:3000], context_run_id=run.context_run_id)
    return redirect(url_for("ai_agents.run_detail", run_id=new_run.id))


@bp.route("/runs")
def history():
    query = AgentRun.query.filter_by(company_id=current_user.id)
    agent_filter = request.args.get("agent", "")
    status_filter = request.args.get("status", "")
    saved_only = request.args.get("saved") == "1"
    if agent_filter:
        query = query.filter(AgentRun.agent_slug == agent_filter)
    if status_filter == "in_progress":
        query = query.filter(AgentRun.status.in_(("queued", "running")))
    elif status_filter in ("succeeded", "failed"):
        query = query.filter(AgentRun.status == status_filter)
    if saved_only:
        query = query.filter(AgentRun.is_saved.is_(True))

    page = max(request.args.get("page", 1, type=int), 1)
    total = query.count()
    runs = (query.order_by(AgentRun.created_at.desc())
            .offset((page - 1) * HISTORY_PAGE_SIZE).limit(HISTORY_PAGE_SIZE).all())
    return render_template("ai_agents/history.html", runs=runs, page=page, total=total,
                           page_size=HISTORY_PAGE_SIZE, agent_filter=agent_filter, status_filter=status_filter,
                           saved_only=saved_only, agent_names=_agent_names())


@bp.route("/runs/<int:run_id>")
def run_detail(run_id):
    run = service.expire_if_stale(service.company_run_or_404(run_id, current_user.id))
    agent = get_agent(run.agent_slug)
    output = service.parsed_output(agent, run) if agent else None
    draft = agent.task_draft(output) if (agent and output) else None
    created_task = None
    if run.created_task_id:
        Task = current_app.extensions["ai_agents"]["task_model"]
        created_task = Task.query.filter_by(id=run.created_task_id, company_id=current_user.id).first()
    can_rerun = agent is not None and _agent_available(agent.slug)
    parent = AgentRun.query.filter_by(id=run.parent_run_id, company_id=current_user.id).first() if run.parent_run_id else None
    context = AgentRun.query.filter_by(id=run.context_run_id, company_id=current_user.id).first() if run.context_run_id else None
    revisions = AgentRun.query.filter_by(parent_run_id=run.id, company_id=current_user.id).filter(
        AgentRun.refinement.isnot(None)).order_by(AgentRun.id).all()
    continue_with = [a for a, _ in service.agents_for_company(current_user.id)
                     if a.accepts_context and a.slug != run.agent_slug] if output is not None else []
    return render_template("ai_agents/run_detail.html", run=run, agent=agent, output=output, draft=draft,
                           created_task=created_task, can_rerun=can_rerun, artifacts=run.artifacts or {},
                           parent=parent, context=context, revisions=revisions, continue_with=continue_with,
                           agent_names=_agent_names())


@bp.route("/runs/<int:run_id>/status")
def run_status(run_id):
    # Polled by the result page while a run is queued or running
    run = service.expire_if_stale(service.company_run_or_404(run_id, current_user.id))
    return jsonify({"id": run.id, "status": run.status, "done": run.status in service.TERMINAL_STATUSES})


@bp.route("/runs/<int:run_id>/save", methods=["POST"])
def toggle_saved(run_id):
    run = service.company_run_or_404(run_id, current_user.id)
    run.is_saved = not run.is_saved
    db.session.commit()
    flash("Result saved." if run.is_saved else "Result removed from saved.", "success")
    return redirect(url_for("ai_agents.run_detail", run_id=run.id))


@bp.route("/runs/<int:run_id>/edit", methods=["GET", "POST"])
def edit_report(run_id):
    run = service.company_run_or_404(run_id, current_user.id)
    agent = get_agent(run.agent_slug)
    output = service.parsed_output(agent, run) if agent else None
    if output is None:
        flash("Only completed results can be edited.", "warning")
        return redirect(url_for("ai_agents.run_detail", run_id=run.id))

    if request.method == "POST":
        if request.form.get("action") == "reset":
            run.edited_report = None
            flash("Edits discarded. The original AI result is shown.", "info")
        else:
            text = (request.form.get("report") or "").strip()
            if not text:
                flash("The edited result cannot be empty.", "danger")
                return render_template("ai_agents/edit_report.html", run=run, agent=agent, report=text), 400
            run.edited_report = text[:500_000]
            flash("Edited result saved. The original AI output is kept for reference.", "success")
        db.session.commit()
        return redirect(url_for("ai_agents.run_detail", run_id=run.id))

    report = run.edited_report or agent.to_markdown(output, artifacts=run.artifacts)
    return render_template("ai_agents/edit_report.html", run=run, agent=agent, report=report)


@bp.route("/runs/<int:run_id>/export")
def export_run(run_id):
    run = service.company_run_or_404(run_id, current_user.id)
    agent = get_agent(run.agent_slug)
    output = service.parsed_output(agent, run) if agent else None
    if run.status != "succeeded":
        abort(404)
    fmt = request.args.get("format", "md")
    base_name = f"step-{run.agent_slug}-run-{run.id}"

    if fmt == "json":
        payload = {
            "run_id": run.id,
            "agent": run.agent_slug,
            "agent_version": run.agent_version,
            "created_at": run.created_at.isoformat() + "Z",
            "model": run.model,
            "input": run.input_data,
            "output": run.output_data,
            "edited_report": run.edited_report,
        }
        body, mimetype, ext = json.dumps(payload, indent=2, ensure_ascii=False), "application/json", "json"
    else:
        if run.edited_report:
            body = run.edited_report
        elif output is not None:
            body = agent.to_markdown(output, artifacts=run.artifacts)
        else:
            abort(404)
        mimetype, ext = "text/markdown; charset=utf-8", "md"
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'})


@bp.route("/files/<int:file_id>")
def download_file(file_id):
    f = service.company_file_or_404(file_id, current_user.id)
    try:
        return send_file(stored_path(f), mimetype=f.content_type, as_attachment=True,
                         download_name=f.original_filename)
    except FileNotFoundError:
        abort(404)


def _agent_names():
    return {a.slug: a.name for a in all_agents()}


def _agent_available(slug):
    try:
        service.resolve_agent(slug, current_user.id)
        return True
    except service.AgentUnavailable:
        return False
