# Database models for the AI Agents feature.
# All tables are new (prefixed ai_agent*) so db.create_all() can add them to an
# existing STEP database without altering users/tasks.

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.dialects import mysql

from extensions import db

def utcnow() -> datetime:
    # Naive UTC, matching how STEP's DATETIME columns are stored
    return datetime.now(timezone.utc).replace(tzinfo=None)


# MySQL TEXT caps at 64KB; extracted documents and edited reports can exceed that
LongText = db.Text().with_variant(mysql.LONGTEXT(), "mysql")


class AIAgent(db.Model):
    # Catalogue row for an agent defined in code (ai_agents/agents/*).
    # Behaviour (prompts, schemas) lives in code; availability lives here so an
    # admin can disable or restrict an agent without a deploy.
    __tablename__ = "ai_agents"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(64))
    icon = db.Column(db.String(64))
    version = db.Column(db.String(20), nullable=False)
    # "active" or "disabled"
    status = db.Column(db.String(20), nullable=False, default="active")
    # "all" = every company, "restricted" = only companies in ai_agent_access
    access_scope = db.Column(db.String(20), nullable=False, default="all")
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())


class AIAgentAccess(db.Model):
    # Allowlist of companies for agents whose access_scope is "restricted"
    __tablename__ = "ai_agent_access"
    __table_args__ = (db.UniqueConstraint("agent_id", "company_id", name="uq_agent_company"),)

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


# Which uploaded files were explicitly attached to which run
run_files = db.Table(
    "ai_agent_run_files",
    db.Column("run_id", db.Integer, db.ForeignKey("ai_agent_runs.id", ondelete="CASCADE"), primary_key=True),
    db.Column("file_id", db.Integer, db.ForeignKey("ai_agent_files.id", ondelete="CASCADE"), primary_key=True),
)


class AgentFile(db.Model):
    # A document a company uploaded for agent use. Text is extracted once at upload.
    __tablename__ = "ai_agent_files"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    # Random name on disk; the original filename is never used as a path
    stored_name = db.Column(db.String(64), nullable=False, unique=True)
    extension = db.Column(db.String(10), nullable=False)
    content_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    extracted_text = db.Column(LongText)
    extracted_chars = db.Column(db.Integer)
    # True when the extracted text was cut to the configured limit
    truncated = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())


class AgentRun(db.Model):
    # One execution of an agent for a company: input, output, cost and outcome
    __tablename__ = "ai_agent_runs"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey("ai_agents.id"), nullable=False, index=True)
    # Snapshot of which agent code produced the output
    agent_slug = db.Column(db.String(64), nullable=False)
    agent_version = db.Column(db.String(20), nullable=False)

    # queued -> running -> succeeded | failed
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    title = db.Column(db.String(200))

    input_data = db.Column(db.JSON, nullable=False)
    output_data = db.Column(db.JSON)
    # Deterministic data STEP computed for the run (data profile, charts, web sources, fact checks)
    artifacts = db.Column(db.JSON)
    # Company-edited version of the report; the original AI output is never overwritten
    edited_report = db.Column(LongText)

    error_code = db.Column(db.String(64))
    error_message = db.Column(db.Text)

    provider = db.Column(db.String(32))
    model = db.Column(db.String(64))
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    web_search_requests = db.Column(db.Integer)
    cost_usd = db.Column(db.Numeric(10, 6))
    duration_ms = db.Column(db.Integer)

    is_saved = db.Column(db.Boolean, default=False, nullable=False)
    # STEP task created from this run's output (AI -> human handoff)
    created_task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="SET NULL"))
    # Set when the run was started via "Run again"
    parent_run_id = db.Column(db.Integer, db.ForeignKey("ai_agent_runs.id", ondelete="SET NULL"))

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    # host:pid of the process that claimed the run from the queue
    worker_id = db.Column(db.String(100))

    agent = db.relationship("AIAgent")
    files = db.relationship("AgentFile", secondary=run_files, lazy="selectin")
