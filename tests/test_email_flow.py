"""
End-to-end check of the STEP email system.

Runs the real Flask routes through the test client against a throwaway SQLite
database, with the console email provider in synchronous mode, then asserts
who received what. No network, no real mail.

    python tests/test_email_flow.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(tempfile.gettempdir(), "step_email_flow_test.db")

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

# Set before importing app so load_dotenv() (override=False) cannot replace them
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH.replace("\\", "/")
os.environ["EMAIL_PROVIDER"] = "console"
os.environ["EMAIL_SYNC"] = "1"
os.environ["APP_URL"] = "https://step-demo.onrender.com"
os.environ["SECRET_KEY"] = "test-secret"
for k in ("RESEND_API_KEY", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SERVER"):
    os.environ.pop(k, None)

sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app as step  # noqa: E402
from email_service import get_provider, describe_provider  # noqa: E402

app = step.app
app.config["TESTING"] = True
client = app.test_client()

with app.app_context():
    step.db.create_all()

provider = get_provider()
print("provider:", describe_provider())
assert provider.name == "console", provider.name


def sent(predicate):
    return [m for m in provider.sent if predicate(m)]


def register(name, email, role, **extra):
    data = {"name": name, "email": email, "password": "Passw0rd!", "role": role}
    data.update(extra)
    r = client.post("/register", data=data, follow_redirects=False)
    assert r.status_code in (302, 303), (email, r.status_code, r.data[:300])


def login(email):
    r = client.post("/login", data={"email": email, "password": "Passw0rd!"})
    assert r.status_code in (302, 303), (email, r.status_code)


def logout():
    client.get("/logout")


# 1. Welcome emails ---------------------------------------------------------
register("Ada Lovelace", "ada@ucc.ie", "student", skills="Python, Flask, SQL")
register("Bob Builder", "bob@ucc.ie", "student", skills="Figma, Design")
register("Acme Ltd", "hire@acme.ie", "company")
register("UCC Careers", "careers@ucc.ie", "university", department="Careers")

welcomes = sent(lambda m: m.subject.startswith("Welcome to STEP"))
assert {m.to for m in welcomes} == {"ada@ucc.ie", "bob@ucc.ie", "hire@acme.ie", "careers@ucc.ie"}, welcomes
assert "Complete your portfolio" in next(m for m in welcomes if m.to == "ada@ucc.ie").text
assert "Post your first task" in next(m for m in welcomes if m.to == "hire@acme.ie").text
assert "https://step-demo.onrender.com/student/portfolio/edit" in next(m for m in welcomes if m.to == "ada@ucc.ie").html
print("OK  welcome emails:", len(welcomes))

# 2. Task posted -> only matching students ------------------------------------
login("hire@acme.ie")
r = client.post("/add-task", data={
    "title": "Build a Flask REST API",
    "description": "Small REST API in Python backed by Postgres for our booking system.",
    "requirements": "Flask, SQL, unit tests",
    "tags": "python, flask, sql",
    "estimated_hours": "12",
    "payment_type": "fixed",
    "fixed_price": "350",
})
assert r.status_code in (302, 303), r.data[:300]
logout()

alerts = sent(lambda m: m.subject.startswith("New task matches your skills"))
assert [m.to for m in alerts] == ["ada@ucc.ie"], [m.to for m in alerts]
assert "Skills matched" in alerts[0].text and "python" in alerts[0].text
assert "https://step-demo.onrender.com/task/1" in alerts[0].html
assert "€350.00 fixed price" in alerts[0].text
with app.app_context():
    t = step.db.session.get(step.Task, 1)
    assert t.tags == "python, flask, sql" and t.payment_type == "fixed"
    ada = step.User.query.filter_by(email="ada@ucc.ie").first()
    assert step.Notification.query.filter_by(user_id=ada.id, task_id=1).count() == 1
print("OK  task alert only to matching student (Ada), in-app notification created")

# 3. Applications -> company notified ----------------------------------------
for who in ("ada@ucc.ie", "bob@ucc.ie"):
    login(who)
    r = client.post("/task/1/apply")
    assert r.status_code in (302, 303)
    logout()
received = sent(lambda m: m.subject.startswith("New application:"))
assert len(received) == 2 and all(m.to == "hire@acme.ie" for m in received)
assert "https://step-demo.onrender.com/company/applicants/1" in received[0].html
print("OK  application-received emails to company:", len(received))

# 4. Company selects Ada -> Ada selected, Bob not selected --------------------
with app.app_context():
    ada_app_id = step.Application.query.filter_by(task_id=1, student_id=ada.id).first().id
login("hire@acme.ie")
r = client.post(f"/company/application/{ada_app_id}/accept")
assert r.status_code in (302, 303), r.data[:300]
logout()

selected = sent(lambda m: m.subject.startswith("You've been selected"))
not_selected = sent(lambda m: m.subject.startswith("Update on your application"))
assert [m.to for m in selected] == ["ada@ucc.ie"], selected
assert [m.to for m in not_selected] == ["bob@ucc.ie"], not_selected
assert "https://step-demo.onrender.com/task/1" in selected[0].html
assert "Browse open tasks" in not_selected[0].text
with app.app_context():
    bob = step.User.query.filter_by(email="bob@ucc.ie").first()
    assert step.Notification.query.filter_by(user_id=bob.id, task_id=1).count() == 1
    assert step.db.session.get(step.Task, 1).status == "in_progress"
print("OK  selection decision: 1 selected, 1 not selected, in-app rows created")

# 5. Direct reject on a second (hourly) task ----------------------------------
login("hire@acme.ie")
client.post("/add-task", data={"title": "Design a landing page", "description": "Figma mockups and CSS.",
                               "tags": "design, frontend", "payment_type": "hourly", "hourly_rate": "20"})
logout()
design_alerts = sent(lambda m: m.subject == "New task matches your skills: Design a landing page")
assert [m.to for m in design_alerts] == ["bob@ucc.ie"], [m.to for m in design_alerts]
assert "€20.00 per hour" in design_alerts[0].text
login("bob@ucc.ie"); client.post("/task/2/apply"); logout()
with app.app_context():
    bob_app_id = step.Application.query.filter_by(task_id=2, student_id=bob.id).first().id
login("hire@acme.ie")
r = client.post(f"/company/application/{bob_app_id}/reject")
assert r.status_code in (302, 303)
logout()
rejected = sent(lambda m: m.subject == "Update on your application: Design a landing page")
assert [m.to for m in rejected] == ["bob@ucc.ie"]
print("OK  direct reject email + hourly task alert")

# 6. Escaping -----------------------------------------------------------------
login("hire@acme.ie")
client.post("/add-task", data={"title": "<script>alert(1)</script> Python job", "description": "x", "tags": "python"})
logout()
evil = sent(lambda m: "alert(1)" in m.subject)
assert evil and "<script>" not in evil[0].html and "&lt;script&gt;" in evil[0].html
print("OK  user text is HTML-escaped in email bodies")

print()
print("ALL SENT (to, subject):")
for m in provider.sent:
    print(f"  {m.to:18} {m.subject}")
print(f"\nPASS: {len(provider.sent)} emails, provider={provider.name}, sender={provider.sent[0].sender}")
