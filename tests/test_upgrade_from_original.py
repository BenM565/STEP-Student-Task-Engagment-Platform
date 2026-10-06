# Upgrading a database created by the original STEP code (before AI agents and
# the work loop) must keep its data and leave every page working.

from conftest import CSRF, login, step_app
from sqlalchemy import inspect, text
from sqlalchemy.dialects import mysql

from core.models import Task, User
from core.schema import add_column_ddl

# Exactly what the original app.py's db.create_all() produced
ORIGINAL_SCHEMA = [
    """CREATE TABLE users (id INTEGER NOT NULL, role VARCHAR(20) NOT NULL, name VARCHAR(120) NOT NULL,
       email VARCHAR(120) NOT NULL, password_hash VARCHAR(255) NOT NULL, skills TEXT, grades VARCHAR(120),
       projects TEXT, "references" TEXT, verified BOOLEAN, PRIMARY KEY (id))""",
    """CREATE TABLE tasks (id INTEGER NOT NULL, title VARCHAR(200) NOT NULL, requirements TEXT,
       estimated_hours INTEGER, company_id INTEGER NOT NULL, PRIMARY KEY (id),
       FOREIGN KEY(company_id) REFERENCES users (id))""",
    """CREATE TABLE applications (id INTEGER NOT NULL, task_id INTEGER NOT NULL, student_id INTEGER NOT NULL,
       status VARCHAR(20), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id),
       FOREIGN KEY(task_id) REFERENCES tasks (id), FOREIGN KEY(student_id) REFERENCES users (id))""",
    """CREATE TABLE disputes (id INTEGER NOT NULL, task_id INTEGER, raised_by_user_id INTEGER NOT NULL,
       against_user_id INTEGER, status VARCHAR(20) NOT NULL, message TEXT NOT NULL,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP, resolved_at DATETIME, resolved_by_admin_id INTEGER,
       resolution_note TEXT, PRIMARY KEY (id))""",
]


def _original_database():
    db = step_app.db
    db.session.remove()
    db.drop_all()
    for ddl in ORIGINAL_SCHEMA:
        db.session.execute(text(ddl))
    hashed = User(name="x", email="x", role="student")
    hashed.set_password("pw")
    rows = [
        "INSERT INTO users (id, role, name, email, password_hash, verified) VALUES (1, 'company', 'Acme', 'co@acme.ie', :h, 0)",
        "INSERT INTO users (id, role, name, email, password_hash, verified) VALUES (2, 'student', 'Sam', 'sam@ucc.ie', :h, 1)",
        "INSERT INTO tasks (id, title, requirements, estimated_hours, company_id) VALUES (1, 'Old open task', 'r', 5, 1)",
        "INSERT INTO tasks (id, title, requirements, estimated_hours, company_id) VALUES (2, 'Old assigned task', 'r', 5, 1)",
        "INSERT INTO applications (id, task_id, student_id, status) VALUES (1, 2, 2, 'accepted')",
        "INSERT INTO disputes (id, raised_by_user_id, status, message) VALUES (1, 2, 'open', 'Old dispute')",
    ]
    for sql in rows:
        db.session.execute(text(sql), {"h": hashed.password_hash})
    db.session.commit()


def test_upgrade_keeps_data_and_backfills_status(app, client):
    _original_database()
    step_app.setup_database()

    inspector = inspect(step_app.db.engine)
    task_columns = {c["name"] for c in inspector.get_columns("tasks")}
    assert {"status", "deadline", "created_at", "completed_at"} <= task_columns
    assert {"about", "website"} <= {c["name"] for c in inspector.get_columns("users")}
    for table in ("submissions", "reviews", "notifications", "ai_agents", "ai_agent_runs"):
        assert table in inspector.get_table_names()

    step_app.db.session.expire_all()
    assert step_app.db.session.get(Task, 1).status == "open"
    assert step_app.db.session.get(Task, 2).status == "in_progress"  # had an accepted application
    assert step_app.setup_database() is None  # idempotent

    # Existing accounts still log in and every key page works
    company = User.query.filter_by(email="co@acme.ie").one()
    login(client, company)
    assert "Old assigned task" in client.get("/dashboard").get_data(as_text=True)
    assert client.get("/company/tasks/2").status_code == 200
    client.get("/logout")
    login(client, User.query.filter_by(email="sam@ucc.ie").one())
    assert "Old assigned task" in client.get("/my/work").get_data(as_text=True)
    assert client.get("/my/tasks/2").status_code == 200
    assert client.post("/tasks/1/apply", data={"csrf_token": CSRF}).status_code == 302


def test_generated_mysql_ddl():
    dialect = mysql.dialect()
    assert add_column_ddl(Task.__table__, "status", dialect) == \
        "ALTER TABLE tasks ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'open'"
    assert add_column_ddl(Task.__table__, "deadline", dialect) == "ALTER TABLE tasks ADD COLUMN deadline DATE"
    assert add_column_ddl(User.__table__, "website", dialect) == "ALTER TABLE users ADD COLUMN website VARCHAR(255)"
