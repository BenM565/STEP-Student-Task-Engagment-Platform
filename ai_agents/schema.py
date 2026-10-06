# Database setup for the AI tables: create missing tables and add AI columns
# introduced after the first release (see core/schema.py for the mechanism).

from core.schema import add_missing_columns
from extensions import db

from .models import AgentRun

# Columns added to ai_agent_runs after the initial AI Agents release
AGENT_RUN_ADDITIVE_COLUMNS = ("artifacts", "web_search_requests", "started_at", "worker_id",
                              "refinement", "context_run_id")


def ensure_schema() -> list:
    """Create missing tables and add missing AI columns. Returns the columns added."""
    db.create_all()
    return add_missing_columns(AgentRun, AGENT_RUN_ADDITIVE_COLUMNS)
