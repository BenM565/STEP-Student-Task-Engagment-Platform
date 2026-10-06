# Additive schema upgrades for databases created before a column existed.
# STEP has no migration tool and db.create_all() never alters existing tables, so
# columns added later are applied here with ALTER TABLE ... ADD COLUMN. Only
# additive changes are supported: nullable columns, or NOT NULL with a constant
# server default. If STEP adopts Flask-Migrate, replace this module with migrations.

from sqlalchemy import inspect, text

from extensions import db

from .models import Application, Task, User

# model -> columns added after the table was first released
CORE_ADDITIVE_COLUMNS = {
    User: ("about", "website", "created_at"),
    Task: ("status", "deadline", "created_at", "completed_at"),
    Application: ("cover_note",),
}


def add_column_ddl(table, name, dialect) -> str:
    """ALTER TABLE statement adding one column, valid on MySQL and SQLite."""
    column = table.c[name]
    ddl = f"ALTER TABLE {table.name} ADD COLUMN {name} {column.type.compile(dialect=dialect)}"
    if column.server_default is not None:
        default = column.server_default.arg
        ddl += f" NOT NULL DEFAULT '{default}'" if not column.nullable else f" DEFAULT '{default}'"
    elif not column.nullable:
        raise RuntimeError(f"Cannot add NOT NULL column {table.name}.{name} without a server default")
    return ddl


def add_missing_columns(model, column_names) -> list:
    """ALTER TABLE to add any of column_names missing from model's table. Returns the columns added."""
    table = model.__table__
    existing = {c["name"] for c in inspect(db.engine).get_columns(table.name)}
    dialect = db.engine.dialect
    added = []
    for name in column_names:
        if name in existing:
            continue
        db.session.execute(text(add_column_ddl(table, name, dialect)))
        added.append(f"{table.name}.{name}")
    db.session.commit()
    return added


def ensure_core_schema() -> list:
    """Create missing tables and add missing core columns, then backfill derived values."""
    db.create_all()
    added = []
    for model, columns in CORE_ADDITIVE_COLUMNS.items():
        added += add_missing_columns(model, columns)
    if "tasks.status" in added:
        # Existing tasks with a selected student are already in progress
        db.session.execute(text(
            "UPDATE tasks SET status = 'in_progress' WHERE id IN "
            "(SELECT task_id FROM applications WHERE status = 'accepted')"
        ))
        db.session.commit()
    return added
