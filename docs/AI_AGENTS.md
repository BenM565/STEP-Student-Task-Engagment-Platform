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
  __init__.py          init_ai_agents(): config, blueprint, CLI
  models.py            AIAgent, AIAgentAccess, AgentRun, AgentFile (+ run↔file link table)
  registry.py          BaseAgent, InputField, @register_agent, shared guardrails
  service.py           access checks, validation, rate limit, execute_run(), company-scoped lookups
  files.py             upload validation, storage, text extraction (txt/md/csv/pdf/docx)
  task_bridge.py       StepTaskDraft schema + mapping onto the existing task form
  security.py          company-only guard, CSRF for this area
  routes.py            catalogue, workspace, history, result, save/edit/export, file download
  providers/
    base.py            AIProvider interface: generate(), structured_output(); ProviderError
    anthropic_provider.py
  agents/
    business_analyst.py
    requirements_to_task.py
templates/ai_agents/   pages + one result template per agent
tests/                 pytest suite (53 tests)
```

## Agent framework

An agent is a `BaseAgent` subclass:

- **Metadata:** `slug`, `name`, `description`, `category`, `icon`, `version`, `capabilities`, `example_use_cases`
- **Contract:** `input_model` and `output_model` (Pydantic). The output model is passed to the provider as a JSON schema, so the model's response is schema-constrained and validated, not free text.
- **Behaviour:** `system_prompt`, `build_prompt(data, documents)`, `to_markdown(output)`, and optionally `task_draft(output)`, which returns a `StepTaskDraft` to enable "Create STEP task".
- **Workspace form:** `input_fields` (rendered generically), `accepts_files`, `allowed_extensions`, `max_files`
- **Display:** `result_template`

### Adding an agent

1. Create `ai_agents/agents/<name>.py` with input/output models and a `@register_agent` subclass.
2. Import it in `ai_agents/agents/__init__.py`.
3. Add `templates/ai_agents/results/<name>.html`.

The catalogue row is created automatically on first page load (or with `flask --app app ai-agents sync`). Routes, service and database need no changes.

### Availability and access

Behaviour lives in code; availability lives in the `ai_agents` table:

```
flask --app app ai-agents sync                       # create tables, list agents
flask --app app ai-agents set-status <slug> disabled
flask --app app ai-agents set-access <slug> restricted
flask --app app ai-agents grant <slug> <company_user_id>
```

## Execution flow

1. Company-only guard and CSRF check (`security.py`).
2. `resolve_agent` checks the agent is active and the company has access.
3. Input validated against `input_model`. Unknown form fields are dropped.
4. Per-company daily run limit.
5. Uploads validated and extracted. Reused files must belong to the same company.
6. An `AgentRun` row is saved as `running`.
7. The provider is called with the agent's system prompt plus shared guardrails, and the input and documents wrapped in `<company_input>` / `<document>` tags.
8. The output is validated against `output_model` and stored as JSON, with tokens, estimated cost, model and duration.
9. On any failure the run is stored as `failed` with a safe message for the user; technical detail goes to the server log only. A run is never left in `running`.

Execution is **synchronous** inside the request. That is simple and fine for the dev server and modest load. Behind gunicorn or a reverse proxy, set the worker/proxy timeout above `AI_TIMEOUT_SECONDS`. `execute_run` is self-contained, so moving it to a job queue (RQ/Celery) later is a contained change.

## AI provider

`AIProvider` exposes `generate()` and `structured_output()`. `AnthropicProvider` implements both with the official `anthropic` SDK (`client.beta.messages.parse` with a Pydantic `output_format`). It also enables the server-side refusal fallback (`fallbacks="default"`): if the primary model declines a request, the API retries it on a fallback model in the same call. It maps SDK errors (auth, rate limit, timeout, refusal, truncation) to `ProviderError` codes.

To add a provider, implement `AIProvider` and register a factory in `providers/__init__.py` (`PROVIDER_FACTORIES`). Select it with `AI_PROVIDER`.

## Configuration

See `.env.example`. Required: `ANTHROPIC_API_KEY`. Without it, runs fail with "The AI service is not configured" rather than crashing. Default model `claude-opus-5-5`, effort `high`. Cost estimates use a built-in price table for current Claude models (checked September 2026) and can be overridden with `AI_INPUT_PRICE_PER_MTOK` / `AI_OUTPUT_PRICE_PER_MTOK`.

## Database

New tables only; existing tables are untouched. There is no migration tool in STEP, and `db.create_all()` (run in `app.py`'s `__main__`, or `flask ai-agents sync`) creates missing tables without altering existing ones.

| Table | Purpose |
|---|---|
| `ai_agents` | Catalogue: slug, name, version, `status` (active/disabled), `access_scope` (all/restricted) |
| `ai_agent_access` | Company allowlist for restricted agents |
| `ai_agent_runs` | One row per run: company, user, agent + version, status, input JSON, output JSON, edited report, error code/message, provider, model, tokens, cost, duration, saved flag, `created_task_id` (→ tasks, `ON DELETE SET NULL`), `parent_run_id` |
| `ai_agent_files` | Uploaded documents: company, original name, random stored name, type, size, SHA-256, extracted text, truncation flag |
| `ai_agent_run_files` | Which files were attached to which run |

No separate messages table: agents are single-shot workers, not chats. There is no separate outputs table either, because each run has exactly one output.

**Requires MySQL 8.0.13+**, the same requirement the existing `applications.created_at` default already has.

## Security

- **Company isolation:** every run/file lookup goes through `company_run_or_404` / `company_file_or_404` / `company_files`, filtered by `company_id`. Another company's IDs return 404. The task pre-fill and task-linking paths apply the same check.
- **Explicit data access:** an agent sees only the text the company typed and the documents attached to that run. It has no database or tool access.
- **No code execution:** agents have no tools. Output is schema-validated JSON rendered through Jinja autoescaping. The AI never creates a task; the company submits the normal form.
- **Prompt injection:** company text and documents are delimited, and the system prompt treats instructions inside them as material. The worst an injection can do is distort that run's analysis, which the company reviews.
- **Uploads:** extension allowlist per agent, size cap, content checks (PDF header, DOCX zip structure, zip-bomb limit, no binary in text files), random stored filenames outside `/static`, download only as an attachment by the owning company.
- **Secrets:** API key read from the environment on the server only.
- **CSRF:** all POSTs in the AI area require a session token.
- **Cost control:** per-company daily run cap; the submit button disables to prevent double runs.

## Known gaps and recommendations

- `.env` with a database password and `FLASK_SECRET=secret` is committed to git. Rotate both and remove the file from tracking.
- Anyone can register as `admin` through `/register`.
- The rest of STEP has no CSRF protection, and `select_candidate` changes data on a GET request.
- `base.html` has an unclosed `data-theme="dark` attribute, references a missing `static/css/theme.css`, and loads Tailwind, DaisyUI and Bootstrap together. DaisyUI's `.alert` overrides Bootstrap's layout; the AI templates work around this.
- The `tasks` table only has title/requirements/hours, so the structured task is composed into `requirements`. Adding columns (skills, deliverables, milestones) would need a migration tool such as Flask-Migrate.
- Scanned (image-only) PDFs and XLSX are not supported yet. The Data Analysis agent will need XLSX support via `openpyxl`.
