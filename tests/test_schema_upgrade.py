"""
Checks that an existing (older) database is upgraded in place, with its data
intact, the first time the app handles a request.

This is what happens on Render when a new release adds model fields: the
Postgres database already exists without the new columns, and no manual
migration is run. Uses a throwaway SQLite file with the previous schema.

    python tests/test_schema_upgrade.py
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(tempfile.gettempdir(), "step_schema_upgrade_test.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH.replace("\\", "/")
os.environ["EMAIL_PROVIDER"] = "console"
os.environ["SECRET_KEY"] = "test-secret"
for k in ("RESEND_API_KEY", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SERVER"):
    os.environ.pop(k, None)

sys.path.insert(0, ROOT)
os.chdir(ROOT)

from werkzeug.security import generate_password_hash  # noqa: E402

# --- 1. Build the OLD schema for the two tables that gained columns ----------
con = sqlite3.connect(DB_PATH)
con.executescript(
    """
    CREATE TABLE university (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
                             domain VARCHAR(120) NOT NULL UNIQUE, created_at DATETIME);
    CREATE TABLE "user" (
        id INTEGER PRIMARY KEY, name VARCHAR(150) NOT NULL, email VARCHAR(150) NOT NULL UNIQUE,
        password VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, verified BOOLEAN,
        headline VARCHAR(150), bio TEXT, experience JSON, university_id INTEGER,
        skills TEXT, grades TEXT, projects TEXT, department VARCHAR(120),
        trust_score FLOAT, stripe_customer_id VARCHAR(255), profile_url VARCHAR(120) UNIQUE,
        user_prefs TEXT);
    CREATE TABLE dispute (
        id INTEGER PRIMARY KEY, raised_by_user_id INTEGER NOT NULL, against_user_id INTEGER,
        task_id INTEGER, message TEXT NOT NULL, status VARCHAR(20), resolution_note TEXT,
        severity INTEGER, ai_suggested_resolution TEXT, created_at DATETIME);
    """
)
pw = generate_password_hash("Passw0rd!")
con.execute("INSERT INTO university (id, name, domain) VALUES (1, 'UCC', 'ucc.ie')")
con.execute(
    'INSERT INTO "user" (id, name, email, password, role, verified, skills, trust_score) '
    "VALUES (1, 'Old Student', 'old@ucc.ie', ?, 'student', 1, 'python, sql', 0)", (pw,))
con.execute(
    'INSERT INTO "user" (id, name, email, password, role, verified, trust_score) '
    "VALUES (2, 'Old Company', 'old@corp.ie', ?, 'company', 1, 0)", (pw,))
con.execute("INSERT INTO dispute (id, raised_by_user_id, message, status, severity) "
            "VALUES (1, 1, 'Old dispute text', 'open', 2)")
con.commit()
con.close()

# --- 2. Start the current app on top of it and make a request -----------------
import app as step  # noqa: E402

app = step.app
app.config["TESTING"] = True
client = app.test_client()

r = client.post("/login", data={"email": "old@ucc.ie", "password": "Passw0rd!"})
assert r.status_code in (302, 303), f"login on the old database failed: {r.status_code}"

# --- 3. New columns exist, old rows survive ------------------------------------
con = sqlite3.connect(DB_PATH)
user_cols = {row[1] for row in con.execute('PRAGMA table_info("user")')}
dispute_cols = {row[1] for row in con.execute("PRAGMA table_info(dispute)")}
for col in ("degree", "year", "company_size", "website", "location", "phone", "logo"):
    assert col in user_cols, f"user.{col} was not added"
for col in ("title", "resolved_at", "resolved_by_id"):
    assert col in dispute_cols, f"dispute.{col} was not added"
tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert {"task", "application", "notification", "review", "lecturer_reference"} <= tables, tables
name, skills = con.execute('SELECT name, skills FROM "user" WHERE id=1').fetchone()
assert (name, skills) == ("Old Student", "python, sql"), (name, skills)
assert con.execute("SELECT message FROM dispute WHERE id=1").fetchone()[0] == "Old dispute text"
con.close()
print("OK  new columns added to user and dispute; missing tables created; old rows intact")

# --- 4. The pages that use the new columns work on the upgraded database -----
for url in ("/student", "/student/portfolio/edit", "/edit-profile", "/disputes", "/student-disputes"):
    assert client.get(url).status_code == 200, url
r = client.post("/student/profile/update", data={"degree": "BSc CS", "year": "2"})
assert r.status_code in (302, 303)
with app.app_context():
    assert step.db.session.get(step.User, 1).degree == "BSc CS"
client.get("/logout")

client.post("/login", data={"email": "old@corp.ie", "password": "Passw0rd!"})
r = client.post("/edit-profile", data={"name": "Old Company", "website": "https://corp.ie", "company_size": "small"})
assert r.status_code in (302, 303)
with app.app_context():
    assert step.db.session.get(step.User, 2).website == "https://corp.ie"
print("OK  upgraded database serves pages and saves the new fields")

# --- 5. Running the upgrade twice is harmless -----------------------------------
with app.app_context():
    step.ensure_schema()
    step.ensure_schema()
print("OK  upgrade is idempotent")
print("PASS")
