# Database setup for the AI tables.
# STEP has no migration tool and db.create_all() never alters existing tables, so
# columns added after the first release are added here with ALTER TABLE. Only
# nullable columns without defaults are listed, which is safe on MySQL and SQLite.
# If STEP adopts Flask-Migrate, replace this with a proper migration.

from sqlalchemy import inspect, text

from extensions import db

from .models import AgentRun

# table -> columns added after the initial AI Agents release
ADDITIVE_COLUMNS = {
    AgentRun.__tablename__: ("artifacts", "web_search_requests", "started_at", "worker_id"),
}
_MODELS = {AgentRun.__tablename__: AgentRun}


def ensure_schema() -> list:
    """Create missing tables and add missing AI columns. Returns the columns added."""
    db.create_all()
    inspector = inspect(db.engine)
    added = []
    for table, columns in ADDITIVE_COLUMNS.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        model_table = _MODELS[table].__table__
        for name in columns:
            if name in existing:
                continue
            column = model_table.c[name]
            ddl_type = column.type.compile(dialect=db.engine.dialect)
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
            added.append(f"{table}.{name}")
    db.session.commit()
    return added
