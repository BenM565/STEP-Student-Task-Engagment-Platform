# STEP AI Agents

Companies give a business problem to a specialist AI agent. The agent does the analysis it can do well. Where hands-on work remains, it drafts a STEP task, which the company edits and posts through the normal task form.

```
Company problem → AI agent → structured analysis → human task identified
→ pre-filled STEP task form (company edits) → task posted → student completes → company reviews
```

## How it fits into STEP

STEP is a single Flask app (`app.py`) using Flask-Login, SQLAlchemy/MySQL and Jinja + Bootstrap 5. The AI feature is a self-contained package, `ai_agents/`, registered as a Blueprint. It touches existing code in only these places:

| Existing file | Change |
|---|---|
| `app.py` | `db` moved to `extensions.py` (same object, now shareable); `init_ai_agents(app, task_model=Task)`; `add_task` accepts `?from_run=<id>` to pre-fill, and links the run to the created task; ownership checks added to `view_applicants` / `select_candidate` (they previously let any company read or change another company's applicants) |
| `templates/add_task.html` | Pre-fill values and an "AI pre-filled" banner |
| `templates/base.html`, `dashboard.html` | "AI Agents" links for company accounts |
| `dashboard.html`, `student_tasks.html` | `white-space: pre-line` so multi-section requirements stay readable |

A "company" is a `users` row with `role="company"`, so `company_id` columns reference `users.id`.

## Package layout

```
ai_agents/
  __init__.py          init_ai_agents(): config, blueprint, CLI (sync, worker, live-check, access control)
  models.py            AIAgent, AIAgentAccess, AgentRun, AgentFile (+ run↔file link table)
  schema.py            ensure_schema(): creates AI tables and adds columns introduced after the first release
  registry.py          BaseAgent, AgentContext, AgentResult, InputField, @register_agent, shared guardrails
  service.py           access checks, validation, rate limit, queue (submit/claim/execute/reap), company-scoped lookups
  files.py             upload validation, storage, text extraction (txt/md/csv/pdf/docx/xlsx)
  data_profile.py      deterministic dataset profiling, chart data and figure checks (Data Analysis)
  task_bridge.py       StepTaskDraft, HumanTaskAssessment, NextStep + mapping onto the existing task form
  security.py          company-only guard, CSRF for this area
  routes.py            catalogue, workspace, history, result, status polling, save/edit/export, file download
  providers/
    base.py            AIProvider: generate(), structured_output(), optional research(); ProviderError
    anthropic_provider.py
  agents/
    business_analyst.py  document_analysis.py  market_research.py
    data_analysis.py     requirements_to_task.py
templates/ai_agents/   pages + one result template per agent (+ shared handoff/next-steps partials)
tests/                 pytest suite (107 tests)
```

## Agents

| Agent | Input | How it works | Grounding |
|---|---|---|---|
| Business Analyst | Problem, background, constraints, optional documents | One structured call | Assumptions and open questions listed separately |
| Requirements-to-Task | A vague need | One structured call → scoped STEP task | Separates AI-ready work from student work |
| Document Analyst | 1–5 documents (PDF/DOCX/TXT/MD/CSV), optional questions | One structured call over extracted text | Each extracted fact is checked word-for-word against the document text ("Found in text" / "Check source"); unanswerable questions are marked "Not in documents" |
| Market Researcher | Idea, geography, optional customers/competitors/documents | 1) research call with server-side web search; 2) structured call over the notes | Claims can cite only sources actually retrieved in step 1 (unknown IDs are dropped); uncited points are labelled "AI analysis"; the run fails if no web sources come back |
| Data Analyst | One CSV/XLSX, optional question | STEP profiles the data in Python (types, stats, outliers, time series, group totals, correlations); the model interprets the profile; STEP draws the suggested charts from the real data | Raw rows never go to the model beyond an 8-row sample; findings whose figures don't appear in the profile are flagged "Check figures"; invalid chart suggestions are shown as "Not drawn" with the reason |

Every agent returns a `HumanTaskAssessment` and next steps with an owner (company / AI agent / STEP student), so any of them can hand remaining work to a student through **Create STEP task**.

## Agent framework

An agent is a `BaseAgent` subclass:

- **Metadata:** `slug`, `name`, `description`, `category`, `icon`, `version`, `capabilities`, `example_use_cases`
- **Contract:** `input_model` and `output_model` (Pydantic). The output model is sent to the provider as a JSON schema, so the response is schema-constrained and validated, not free text.
- **Workflow:** `execute(ctx)`. The default is one structured call using `system_prompt` and `build_prompt()`. Override it for multi-step workflows (research then structure, compute then interpret). Call `ctx.record(result)` after each provider call so tokens, searches and cost are tracked even if a later step fails. Return `AgentResult(output, artifacts)`; artifacts hold data STEP computed itself (profiles, charts, sources, fact checks).
- **Handoff and export:** `task_draft(output)` returns a `StepTaskDraft` to enable "Create STEP task"; `to_markdown(output, artifacts)` drives export and editing.
- **Workspace form:** `input_fields` (rendered generically), `accepts_files`, `allowed_extensions`, `min_files`, `max_files`, `files_label`, `files_help`
- **Capabilities:** `requires_web_search` hides the agent when `AI_WEB_SEARCH_ENABLED` is off.
- **Display:** `result_template`; `sample_input` enables `flask ai-agents live-check <slug>`.

### Adding an agent

1. Create `ai_agents/agents/<name>.py` with input/output models and a `@register_agent` subclass.
2. Import it in `ai_agents/agents/__init__.py`.
3. Add `templates/ai_agents/results/<name>.html` (reuse `_human_task.html` and `_next_steps.html`).

The catalogue row is created automatically. Routes, service, queue and database need no changes.

### Availability and access

Behaviour lives in code; availability lives in the `ai_agents` table:

```
flask --app app ai-agents sync                       # create/upgrade tables, list agents
flask --app app ai-agents set-status <slug> disabled
flask --app app ai-agents set-access <slug> restricted
flask --app app ai-agents grant <slug> <company_user_id>
```

## Execution and the job queue

Request path (fast):

1. Company-only guard and CSRF check.
2. `resolve_agent`: agent active, company has access, required capabilities enabled.
3. Input validated against `input_model`; unknown fields dropped; file count checked.
4. Per-company daily run limit.
5. Uploads validated and text extracted. Reused files must belong to the same company.
6. An `AgentRun` is saved as **queued** and handed to the executor; the browser goes to the result page, which polls `/runs/<id>/status` and reloads when the run finishes. Leaving the page is fine.

Execution (in the background):

7. The run is **claimed** with an atomic `UPDATE … WHERE status='queued'`, so it runs exactly once even with several web processes and workers.
8. `agent.execute()` runs with only that run's input and files.
9. Output, artifacts, tokens, web searches, estimated cost, model and duration are stored. Any failure stores `failed` with a safe message; technical detail goes to the server log.
10. Runs stuck in `running` (process died) or `queued` (no worker) past the configured timeouts are failed with an explanation, by the worker loop and whenever the run is viewed.

`AI_EXECUTION_MODE` picks the executor:

| Mode | Where runs execute | Use for |
|---|---|---|
| `thread` (default) | A background thread in the web process | Development and small deployments; nothing extra to run |
| `worker` | `flask --app app ai-agents worker` processes polling the queue | Production. Run as many as you want concurrent runs (e.g. under systemd or supervisor). Survives web restarts; `SIGTERM` finishes the current run first |
| `inline` | Inside the web request | Tests and debugging |

The queue lives in the existing `ai_agent_runs` table, so no Redis or new service is needed. If volume outgrows polling MySQL, `submit_run`/`dispatch` is the one place to swap in RQ or Celery.

In worker mode the upload directory (`AI_UPLOAD_DIR`) must be readable by the workers, which means shared storage if they run on other machines.

## AI provider

`AIProvider` exposes `generate()`, `structured_output()` and an optional `research()`; providers without web research raise a clear `capability_unavailable` error. `AnthropicProvider` uses the official `anthropic` SDK:

- `structured_output`: `client.beta.messages.parse` with a Pydantic `output_format`
- `research`: the server-side `web_search_20260209` tool (with `max_uses`), resuming `pause_turn` responses, and collecting sources from search results and citations
- server-side refusal fallback (`fallbacks="default"`) on every call
- SDK errors (auth, permission, rate limit, timeout, refusal, truncation) mapped to `ProviderError` codes

To add a provider, implement `AIProvider` and register a factory in `providers/__init__.py` (`PROVIDER_FACTORIES`), then select it with `AI_PROVIDER`.

### Checking the live integration

```
export ANTHROPIC_API_KEY=...            # or put it in .env
flask --app app ai-agents live-check                    # Business Analyst
flask --app app ai-agents live-check market-research    # also checks web search is enabled for your org
```

This makes one real call with a sample input, prints model, tokens, searches and estimated cost, and saves nothing.

## Configuration

See `.env.example`. Required: `ANTHROPIC_API_KEY`. Without it, runs fail with "The AI service is not configured" rather than crashing. Default model `claude-opus-5-5`, effort `high`. Cost estimates use a built-in price table for current Claude models plus $10 per 1,000 web searches (Anthropic list prices, checked September 2026), overridable with `AI_INPUT_PRICE_PER_MTOK` / `AI_OUTPUT_PRICE_PER_MTOK` / `AI_WEB_SEARCH_PRICE_PER_1K`.

The Market Researcher also needs web search enabled for your organisation in the Anthropic Console; set `AI_WEB_SEARCH_ENABLED=0` to hide it if you don't want web search.

## Database

New tables only; existing STEP tables are untouched. STEP has no migration tool and `db.create_all()` never alters existing tables, so `ensure_schema()` (run by `app.py`'s `__main__` and by `flask ai-agents sync`) creates missing AI tables and adds AI columns introduced after the first release with `ALTER TABLE … ADD COLUMN` (nullable columns only). If STEP adopts Flask-Migrate, replace it with a proper migration.

| Table | Purpose |
|---|---|
| `ai_agents` | Catalogue: slug, name, version, `status` (active/disabled), `access_scope` (all/restricted) |
| `ai_agent_access` | Company allowlist for restricted agents |
| `ai_agent_runs` | One row per run, and the job queue: company, user, agent + version, status (queued/running/succeeded/failed), input JSON, output JSON, artifacts JSON (STEP-computed data), edited report, error code/message, provider, model, tokens, web searches, cost, timings, worker id, saved flag, `created_task_id` (→ tasks, `ON DELETE SET NULL`), `parent_run_id` |
| `ai_agent_files` | Uploaded documents: company, original name, random stored name, type, size, SHA-256, extracted text, truncation flag |
| `ai_agent_run_files` | Which files were attached to which run |

No separate messages table: agents are single-shot workers, not chats. There is no separate outputs table either, because each run has exactly one output.

**Requires MySQL 8.0.13+**, the same requirement the existing `applications.created_at` default already has.

## Security

- **Company isolation:** every run/file lookup goes through `company_run_or_404` / `company_file_or_404` / `company_files`, filtered by `company_id`. Another company's IDs return 404. The task pre-fill and task-linking paths apply the same check.
- **Explicit data access:** an agent sees only the text the company typed and the documents attached to that run. It has no database or tool access.
- **No code execution:** the only tool any agent uses is Anthropic's server-side web search (Market Researcher). Uploaded spreadsheets are parsed as data (formulas are never evaluated). Output is schema-validated JSON rendered through Jinja autoescaping; source links are restricted to http(s) and open with `rel="noopener noreferrer nofollow"`. The AI never creates a task; the company submits the normal form.
- **Prompt injection:** company text and documents are delimited, and the system prompt treats instructions inside them as material. The worst an injection can do is distort that run's analysis, which the company reviews.
- **Uploads:** extension allowlist per agent, size cap, content checks (PDF header, DOCX/XLSX zip structure, zip-bomb limit, no binary in text files), row/column caps for datasets, random stored filenames outside `/static`, download only as an attachment by the owning company.
- **Secrets:** API key read from the environment on the server only.
- **CSRF:** all POSTs in the AI area require a session token.
- **Cost control:** per-company daily run cap, web search `max_uses` per run, and the submit button disables to prevent double runs.

## Known gaps and recommendations

- `.env` with a database password and `FLASK_SECRET=secret` is committed to git. Rotate both and remove the file from tracking.
- Anyone can register as `admin` through `/register`.
- The rest of STEP has no CSRF protection, and `select_candidate` changes data on a GET request.
- `base.html` has an unclosed `data-theme="dark` attribute, references a missing `static/css/theme.css`, and loads Tailwind, DaisyUI and Bootstrap together. DaisyUI's `.alert` overrides Bootstrap's layout; the AI templates work around this.
- The `tasks` table only has title/requirements/hours, so the structured task is composed into `requirements`. Adding columns (skills, deliverables, milestones) would need a migration tool such as Flask-Migrate.
- Scanned (image-only) PDFs are not supported. Excel analysis reads the first sheet only, using the values Excel last calculated.
- Runs are single-shot: there is no follow-up conversation on a result yet ("Run again" pre-fills the previous input).
