"""
Whole-site smoke test.

Seeds a throwaway SQLite database, then opens every page as every role and submits
every form, checking that each returns a sane status, that submitted values are
actually saved, and that no template references an undefined field. It never
touches the real database.

    python tests/test_site_smoke.py
"""
import io, os, sys, warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import tempfile
SCRATCH = tempfile.gettempdir()
DB = os.path.join(SCRATCH, "step_site_smoke_test.db")
if os.path.exists(DB): os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
os.environ["EMAIL_PROVIDER"] = "console"; os.environ["EMAIL_SYNC"] = "1"
os.environ["SECRET_KEY"] = "t"; os.environ["ADMIN_REGISTRATION_CODE"] = "adm"
for k in ("RESEND_API_KEY", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SERVER"): os.environ.pop(k, None)
sys.path.insert(0, ROOT); os.chdir(ROOT)

import builtins
_real_print = builtins.print
def quiet_print(*a, **k):
    s = " ".join(str(x) for x in a)
    if s.startswith("[STEP email]") or s.startswith("[STEP]") or "EMAIL" in s or s.startswith("=") or "Push]" in s:
        return
    _real_print(*a, **k)

import app as step
from jinja2 import Undefined
from jinja2.utils import missing
from flask import before_render_template

CURRENT = {"t": "?"}
UNDEF = {}
@before_render_template.connect_via(step.app)
def _track(sender, template, context, **extra):
    CURRENT["t"] = template.name

class LoggingUndefined(Undefined):
    def __init__(self, hint=None, obj=missing, name=None, exc=None):
        from jinja2.exceptions import UndefinedError
        super().__init__(hint, obj, name, exc or UndefinedError)
        key = f"{{{{ {name} }}}}" if obj is missing else f"{type(obj).__name__}.{name}"
        UNDEF.setdefault(CURRENT["t"], set()).add(key)

app = step.app
app.config["UPLOAD_FOLDER"] = tempfile.mkdtemp(prefix="step_uploads_")  # never write into the repo
app.jinja_env.undefined = LoggingUndefined
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = True
client = app.test_client()
db = step.db
U, T, A = step.User, step.Task, step.Application

def mk_user(name, email, role, **kw):
    u = U(name=name, email=email, role=role, **kw); u.set_password("Passw0rd!"); db.session.add(u); return u

with app.app_context():
    db.create_all()
    uni = step.University(name="University College Cork", domain="ucc.ie"); db.session.add(uni); db.session.flush()
    ada = mk_user("Ada Lovelace", "ada@ucc.ie", "student", verified=True, headline="Backend dev", bio="Python person",
                  skills="Python, Flask, SQL", grades="1:1", projects="Built a Flask API", university_id=uni.id,
                  experience=[{"job_title": "Intern", "company": "Acme", "start_date": "2025-06", "end_date": "2025-08", "description": "x"}])
    bob = mk_user("Bob Builder", "bob@ucc.ie", "student", skills="Figma, Design", university_id=uni.id)
    acme = mk_user("Acme Ltd", "hire@acme.ie", "company", bio="We build things")
    admin = mk_user("Admin", "admin@step.ie", "admin")
    staff = mk_user("UCC Careers", "careers@ucc.ie", "university", department="Careers", university_id=uni.id)
    db.session.flush()
    t1 = T(title="Open task", description="Python Flask API", requirements="Flask, SQL", tags="python, flask", estimated_hours=10, payment_type="fixed", fixed_price=300, status="open", company_id=acme.id)
    t2 = T(title="Active task", description="Data cleanup", tags="data, python", estimated_hours=5, payment_type="hourly", hourly_rate=20, status="in_progress", company_id=acme.id)
    t3 = T(title="Done task", description="Landing page", tags="html, css", estimated_hours=8, payment_type="fixed", fixed_price=150, status="completed", company_id=acme.id)
    db.session.add_all([t1, t2, t3]); db.session.flush()
    a1 = A(task_id=t1.id, student_id=ada.id, status="pending")
    a1b = A(task_id=t1.id, student_id=bob.id, status="pending")
    a2 = A(task_id=t2.id, student_id=ada.id, status="in_progress", selected=True, submitted_at=datetime.utcnow(), review_status="pending", student_file="app_x.txt", deadline_at=datetime.utcnow()+timedelta(days=3))
    a3 = A(task_id=t3.id, student_id=ada.id, status="completed", review_status="approved", completed_at=datetime.utcnow(), first_pass_success=True, deadline_at=datetime.utcnow()+timedelta(days=1))
    db.session.add_all([a1, a1b, a2, a3]); db.session.flush()
    db.session.add(step.Review(application_id=a3.id, rater_user_id=acme.id, ratee_user_id=ada.id, rating=5, comment="Great"))
    db.session.add(step.Review(application_id=a3.id, rater_user_id=ada.id, ratee_user_id=acme.id, rating=4, comment="Good client"))
    db.session.add(step.Notification(user_id=ada.id, task_id=t2.id, message="You were selected for: Active task"))
    db.session.add(step.Notification(user_id=acme.id, task_id=t1.id, message="New application"))
    d1 = step.Dispute(raised_by_user_id=ada.id, against_user_id=acme.id, task_id=t2.id, title="Payment late", message="Payment late", severity=3, status="open", ai_suggested_resolution="Propose a milestone-based payment plan.")
    db.session.add(d1)
    db.session.add(step.RatingAppeal(application_id=a3.id, student_id=ada.id, reason="I believe the rating was unfair because of X and Y", status="pending"))
    db.session.add(step.ProjectMedia(user_id=ada.id, filename="proj.txt", title="Proj", description="d", link="https://x.y"))
    db.session.add(step.LecturerReference(student_id=ada.id, lecturer_name="Dr Smith"))
    db.session.commit()
    I = dict(uni=uni.id, ada=ada.id, bob=bob.id, acme=acme.id, admin=admin.id, staff=staff.id, t1=t1.id, t2=t2.id, t3=t3.id,
             a1=a1.id, a1b=a1b.id, a2=a2.id, a3=a3.id, d1=d1.id)

builtins.print = quiet_print
BAD = []

def login(email, password="Passw0rd!"):
    client.get("/logout")
    r = client.post("/login", data={"email": email, "password": password})
    assert r.status_code in (302, 303), (email, r.status_code)

def get(url, expect=(200, 302)):
    try:
        r = client.get(url); code = r.status_code; note = ""
    except Exception as e:
        code = 500; note = f"{type(e).__name__}: {str(e).splitlines()[0][:140]}"
    ok = code in expect
    if not ok: BAD.append(f"GET {url} -> {code} {note}")
    _real_print(f"  {'ok ' if ok else 'BAD'} {code} GET  {url} {note}")
    return code

def post(url, data=None, expect=(302, 303, 200), **kw):
    try:
        r = client.post(url, data=data or {}, **kw); code = r.status_code; note = ""
    except Exception as e:
        code = 500; note = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    ok = code in expect
    if not ok: BAD.append(f"POST {url} -> {code} {note}")
    _real_print(f"  {'ok ' if ok else 'BAD'} {code} POST {url} {note}")
    return code

def check(label, cond, detail=""):
    if not cond: BAD.append(f"CHECK {label}: {detail}")
    _real_print(f"  {'PASS' if cond else 'FAIL'} {label} {detail if not cond else ''}")

_real_print("\n=== PUBLIC ===")
client.get("/logout")
for u in ["/", "/login", "/register", "/browse-tasks", f"/task/{I['t1']}", f"/portfolio/{I['ada']}", "/browse-tasks?q=python&hours_max=20"]:
    get(u)
get("/this-page-does-not-exist", expect=(404,))

_real_print("\n=== STUDENT (ada) ===")
login("ada@ucc.ie")
for u in ["/", "/student", "/student/notifications", "/student/portfolio/edit", "/profile", "/edit-profile",
          "/student/performance-breakdown", "/student-disputes", "/disputes", f"/ratings/{I['a3']}/appeal",
          f"/task/{I['t1']}", f"/task/{I['t2']}", f"/task/{I['t3']}", "/browse-tasks?ai_match=1", "/api/search?q=task", "/dispute/new"]:
    get(u)
get("/company", expect=(403,))

_real_print("\n=== COMPANY (acme) ===")
login("hire@acme.ie")
for u in ["/", "/company", "/add-task", f"/company/applicants/{I['t1']}", f"/company/applicants/{I['t2']}", f"/company/applicants/{I['t3']}",
          "/company/search-students", "/company/search-students?search=ada&skills=python&min_trust_score=0&verified=yes&sort_by=rating",
          "/company/search-students?sort_by=completed_tasks", "/company/students", f"/company/student/{I['ada']}",
          f"/company/task/{I['t1']}/edit", "/profile", "/edit-profile", "/company-disputes", f"/company-disputes/{I['d1']}",
          "/api/search?q=a", "/disputes", f"/task/{I['t1']}", "/dispute/new"]:
    get(u)

_real_print("\n=== ADMIN ===")
login("admin@step.ie")
for u in ["/", "/admin", "/admin/users", f"/admin/users/{I['ada']}", "/admin-disputes", f"/admin/disputes/{I['d1']}", "/admin/appeals",
          "/admin/universities", "/api/search?q=a", "/profile", "/edit-profile", "/disputes"]:
    get(u)

_real_print("\n=== UNIVERSITY (staff) ===")
login("careers@ucc.ie")
for u in ["/", "/university/dashboard", "/university/dashboard?start=2026-01-01&end=2026-12-31",
          f"/university/dashboard/export?university_id={I['uni']}&start=2026-01-01&end=2026-12-31",
          "/university/dashboard/export", "/university/students", f"/university/students/{I['ada']}",
          "/profile", "/edit-profile", "/api/search?q=a"]:
    get(u)
get(f"/university/students/{I['acme']}", expect=(404,))

# ---------------------------------------------------------------- POSTS + persistence
_real_print("\n=== FORMS: student ===")
login("ada@ucc.ie")
post("/student/profile/update", {"skills": "Python, Flask, SQL, Docker"})
post("/student/profile/update", {"headline": "Backend developer", "bio": "New bio", "university": str(I["uni"]), "degree": "BSc CS", "year": "4"})
with app.app_context():
    u = db.session.get(U, I["ada"])
    check("update_student_profile saved skills/headline/bio", u.skills.endswith("Docker") and u.headline == "Backend developer" and u.bio == "New bio")
    check("update_student_profile saved degree/year", u.degree == "BSc CS" and u.year == "4", f"{u.degree},{u.year}")
post("/edit-profile", {"name": "Ada L", "email": "ada@ucc.ie", "university": str(I["uni"]), "grades": "1:1 (new)", "projects": "P2", "degree": "BSc Computer Science", "year": "3",
                       "current_password": "Passw0rd!", "new_password": "Passw0rd!", "confirm_password": "Passw0rd!"})
with app.app_context():
    u = db.session.get(U, I["ada"])
    check("student edit_profile saved name/grades/projects/degree/year", u.name == "Ada L" and u.grades == "1:1 (new)" and u.projects == "P2" and u.degree == "BSc Computer Science" and u.year == "3", f"{u.name},{u.grades},{u.projects},{u.degree},{u.year}")
post("/edit-profile", {"name": "Ada L", "email": "hire@acme.ie"})  # duplicate email must be rejected
with app.app_context():
    check("edit_profile rejects duplicate email", db.session.get(U, I["ada"]).email == "ada@ucc.ie")
post("/edit-profile", {"current_password": "wrong", "new_password": "NewPassw0rd!", "confirm_password": "NewPassw0rd!"})
with app.app_context():
    check("wrong current password keeps old password", db.session.get(U, I["ada"]).check_password("Passw0rd!"))
post("/student/experience/add", {"job_title": "Dev", "company": "X", "start_date": "2026-01", "end_date": "2026-03", "description": "d"})
with app.app_context():
    u = db.session.get(U, I["ada"]); check("add_experience appended", isinstance(u.experience, list) and len(u.experience) == 2, str(u.experience)[:80])
post("/student/experience/0/delete")
with app.app_context():
    u = db.session.get(U, I["ada"]); check("delete_experience removed first entry", len(u.experience) == 1 and u.experience[0]["job_title"] == "Dev", str(u.experience)[:80])
get("/student/portfolio/edit")
post("/student/portfolio/project/add", {"project_title": "Proj2", "project_description": "desc", "project_link": "https://a.b",
                                        "project_file": (io.BytesIO(b"hello"), "notes.txt")}, content_type="multipart/form-data")
with app.app_context():
    pm = step.ProjectMedia.query.filter_by(user_id=I["ada"], title="Proj2").first(); check("add_portfolio_project saved", pm is not None and pm.link == "https://a.b")
    pm_id = pm.id if pm else 0
post(f"/student/portfolio/item/{pm_id}/delete")
with app.app_context():
    check("delete_portfolio_item removed", db.session.get(step.ProjectMedia, pm_id) is None)
post("/student/add-lecturer-reference", {"lecturer_name": "Prof X"})
post("/student/add-lecturer-reference", {"lecturer_name": "prof x"})  # duplicate, case-insensitive
post("/student/add-lecturer-reference", {"lecturer_name": "   "})     # blank
with app.app_context():
    refs = step.LecturerReference.query.filter_by(student_id=I["ada"]).all()
    check("lecturer reference added once, duplicate and blank rejected", sorted(r.lecturer_name for r in refs) == ["Dr Smith", "Prof X"], [r.lecturer_name for r in refs])
    prof_x = next(r.id for r in refs if r.lecturer_name == "Prof X")
html = client.get("/student/portfolio/edit").get_data(as_text=True)
check("portfolio editor lists lecturer references + add form", "Prof X" in html and "add-lecturer-reference" in html)
with app.app_context():
    dr_smith = next(r.id for r in step.LecturerReference.query.filter_by(student_id=I["ada"]).all() if r.lecturer_name == "Dr Smith")
login("bob@ucc.ie")
post(f"/student/lecturer-reference/{dr_smith}/delete", expect=(403,))  # someone else's reference
login("ada@ucc.ie")
post(f"/student/lecturer-reference/{prof_x}/delete")
with app.app_context():
    check("lecturer reference deleted, other student's delete blocked", db.session.get(step.LecturerReference, prof_x) is None and db.session.get(step.LecturerReference, dr_smith) is not None)
post("/student-disputes", {"title": "Late payment", "description": "Payment is late and unpaid", "dispute_type": "payment"})
post("/disputes", {"title": "T2", "description": "Other issue"})
post("/dispute/new", {"title": "Scope creep", "task_id": str(I["t2"]), "message": "Requirements changed after selection"})
with app.app_context():
    ds = step.Dispute.query.filter_by(raised_by_user_id=I["ada"]).order_by(step.Dispute.id).all()
    check("student disputes created (3 new)", len(ds) == 4, len(ds))
    late = next((d for d in ds if d.title == "Late payment"), None)
    check("dispute title + AI triage saved", late is not None and late.severity >= 3 and late.ai_suggested_resolution, (late.title, late.severity, late.ai_suggested_resolution) if late else None)
    scope = next((d for d in ds if d.title == "Scope creep"), None)
    check("dispute_new inferred task + counterparty", scope is not None and scope.task_id == I["t2"] and scope.against_user_id == I["acme"], (scope.task_id, scope.against_user_id) if scope else None)
get("/student-disputes"); get("/disputes")
post(f"/task/{I['t1']}/apply")  # already applied -> flash
get("/browse-tasks?applied=1")
post(f"/student/upload-project-media?application_id={I['a2']}", {"file": (io.BytesIO(b"work"), "work.txt")}, content_type="multipart/form-data")
with app.app_context():
    a = db.session.get(A, I["a2"]); check("upload set review_status pending + file", a.review_status == "pending" and a.student_file.endswith("work.txt"))
post(f"/ratings/{I['a3']}/appeal", {"reason": "This rating does not reflect the work delivered at all."})
with app.app_context():
    nid = step.Notification.query.filter_by(user_id=I["ada"]).first().id
post(f"/student/notifications/{nid}/read")
with app.app_context():
    check("mark_notification_read", db.session.get(step.Notification, nid).is_read)
post("/student/notifications/0/read")
with app.app_context():
    check("mark all read", step.Notification.query.filter_by(user_id=I["ada"], is_read=False).count() == 0)
post(f"/student/application/{I['a1']}/accept-selection")
post(f"/student/application/{I['a1']}/decline-selection")
post(f"/application/{I['a3']}/review", {"rating": "3", "comment": "updated"})
with app.app_context():
    rv = step.Review.query.filter_by(application_id=I["a3"], rater_user_id=I["ada"]).first(); check("student review updated", rv.rating == 3 and rv.comment == "updated")

_real_print("\n=== FORMS: company ===")
login("hire@acme.ie")
post("/edit-profile", {"name": "Acme Limited", "company_size": "small", "website": "https://acme.ie", "bio": "Bio2"})
post("/edit-profile", {"email": "hire@acme.ie", "phone": "+353 1 234", "location": "Cork"})
post("/edit-profile", {"logo": (io.BytesIO(b"\x89PNG"), "logo.png")}, content_type="multipart/form-data")
post("/edit-profile", {"logo": (io.BytesIO(b"nope"), "virus.exe")}, content_type="multipart/form-data")
with app.app_context():
    u = db.session.get(U, I["acme"])
    check("company edit_profile saved name/bio (bio not blanked by other forms)", u.name == "Acme Limited" and u.bio == "Bio2", f"{u.name},{u.bio}")
    check("company edit_profile saved size/website/location/phone", (u.company_size, u.website, u.location, u.phone) == ("small", "https://acme.ie", "Cork", "+353 1 234"), (u.company_size, u.website, u.location, u.phone))
    check("company logo saved and .exe rejected", u.logo and u.logo.endswith("logo.png"), u.logo)
get("/profile"); get("/edit-profile")
post(f"/company/task/{I['t1']}/edit", {"title": "Open task v2", "description": "desc2", "requirements": "Flask, SQL, tests", "estimated_hours": "12", "tags": "python, flask, sql", "payment_type": "hourly", "fixed_price": "", "hourly_rate": "25"})
with app.app_context():
    t = db.session.get(T, I["t1"])
    check("edit_task saved every field", (t.title, t.description, t.requirements, t.estimated_hours, t.tags, t.payment_type, t.fixed_price, t.hourly_rate) == ("Open task v2", "desc2", "Flask, SQL, tests", 12, "python, flask, sql", "hourly", None, 25.0),
          (t.title, t.description, t.requirements, t.estimated_hours, t.tags, t.payment_type, t.fixed_price, t.hourly_rate))
post(f"/company/task/{I['t1']}/edit", {"title": "Open task v2", "estimated_hours": "abc"})
with app.app_context():
    check("edit_task bad number -> friendly error, value kept", db.session.get(T, I["t1"]).estimated_hours == 12)
post(f"/company/application/{I['a2']}/upload-media", {"file": (io.BytesIO(b"brief"), "brief.pdf")}, content_type="multipart/form-data")
with app.app_context():
    a = db.session.get(A, I["a2"]); check("company upload_media saved", a.media_file and a.media_file.endswith("brief.pdf"), a.media_file)
post(f"/company/application/{I['a2']}/review", {"review_status": "changes_requested", "feedback": "Please fix the header"})
with app.app_context():
    a = db.session.get(A, I["a2"]); check("review_submission saved feedback", a.review_feedback == "Please fix the header", repr(a.review_feedback))
post(f"/company/application/{I['a2']}/review", {"review_status": "approved", "review_feedback": "Nice", "rating": "5"})
with app.app_context():
    a = db.session.get(A, I["a2"]); check("review_submission approved -> completed", a.status == "completed" and a.review_status == "approved")
post(f"/application/{I['a3']}/review", {"rating": "4", "comment": "ok"})
with app.app_context():
    ts = db.session.get(U, I["ada"]).trust_score
    check("trust score is calculated and stored after approval / rating", ts and ts > 0, ts)
    from importlib import import_module
    m, _ = step.compute_student_metrics(I["ada"])
    check("stored trust score equals the performance-breakdown weighted score", abs(ts - m["weighted_score"]) < 0.01, (ts, m["weighted_score"]))
client.get("/logout")
html = client.get(f"/portfolio/{I['ada']}").get_data(as_text=True)
check("public portfolio shows headline, bio, degree, experience, project description + link, trust score",
      all(s in html for s in ("Backend developer", "New bio", "BSc Computer Science", "Dev", "https://x.y", f"{ts:.1f}%")),
      [s for s in ("Backend developer", "New bio", "BSc Computer Science", "Dev", "https://x.y") if s not in html])
login("hire@acme.ie")
html = client.get(f"/company/student/{I['ada']}").get_data(as_text=True)
check("company student view shows university, degree, grades, projects, experience",
      all(s in html for s in ("University College Cork", "BSc Computer Science", "1:1 (new)", "P2", "Dev")),
      [s for s in ("University College Cork", "BSc Computer Science", "1:1 (new)", "P2", "Dev") if s not in html])
html = client.get("/company/search-students?min_trust_score=1&sort_by=trust_score").get_data(as_text=True)
check("company search min trust score now finds the student", "Ada" in html)
post("/add-task", {"title": "Quick post", "description": "From search page", "payment_type": "fixed", "fixed_price": "50"})
post("/add-task", {"title": "With file", "description": "d", "tags": "python", "media": (io.BytesIO(b"spec"), "spec.pdf")}, content_type="multipart/form-data")
with app.app_context():
    tf = T.query.filter_by(title="With file").first(); check("add_task saved attachment", tf and tf.media_file and tf.media_file.endswith("spec.pdf"), tf.media_file if tf else None)
post(f"/company/application/{I['a1b']}/reject")
post(f"/company/application/{I['a1']}/accept")
with app.app_context():
    check("accept -> in_progress", db.session.get(A, I["a1"]).status == "in_progress" and db.session.get(T, I["t1"]).status == "in_progress")
post(f"/company/application/{I['a1']}/approve")
from email_service import get_provider
approved_mail = [m for m in get_provider().sent if m.subject.startswith("Approved:")]
check("approval email claims no payment when none was captured",
      approved_mail and "Paid to you" not in approved_mail[-1].text and "Platform fee" not in approved_mail[-1].text)
with app.app_context():
    check("approve -> completed (no referrer redirect works)", db.session.get(A, I["a1"]).status == "completed")
with app.app_context():
    nid = step.Notification.query.filter_by(user_id=I["acme"]).first().id
post(f"/student/notifications/{nid}/read", {"next": "/company#notifications"})
post("/student/notifications/0/read", {"next": "https://evil.example/phish"})
with app.app_context():
    tq = T.query.filter_by(title="Quick post").first().id
post(f"/task/{tq}/delete")
with app.app_context():
    check("delete_task", db.session.get(T, tq) is None)
post("/dispute/new", {"title": "No show", "task_id": str(I["t2"]), "message": "Student stopped responding"})
with app.app_context():
    d = step.Dispute.query.filter_by(title="No show").first()
    check("company dispute_new inferred selected student", d and d.against_user_id == I["ada"] and d.task_id == I["t2"], (d.against_user_id, d.task_id) if d else None)

_real_print("\n=== FORMS: admin ===")
login("admin@step.ie")
post(f"/admin/users/{I['bob']}/verify"); post(f"/admin/users/{I['bob']}/unverify")
post(f"/admin/disputes/{I['d1']}", {"action": "apply_suggestion", "resolution_note": ""})
with app.app_context():
    d = db.session.get(step.Dispute, I["d1"]); check("apply_suggestion copied note + in_review", d.status == "in_review" and d.resolution_note.startswith("Propose"), (d.status, d.resolution_note))
post(f"/admin/disputes/{I['d1']}", {"action": "resolve_with_suggestion", "resolution_note": "Sorted"})
with app.app_context():
    d = db.session.get(step.Dispute, I["d1"])
    check("admin resolve dispute (+ resolved_at, resolved_by persisted)", d.status == "resolved" and "Sorted" in d.resolution_note and d.resolved_at is not None and d.resolved_by_id == I["admin"], (d.status, d.resolution_note, d.resolved_at, d.resolved_by_id))
    check("dispute parties notified", step.Notification.query.filter(step.Notification.message.like("Dispute #%")).count() >= 2)
with app.app_context():
    ap = step.RatingAppeal.query.first().id
post(f"/admin/appeals/{ap}/resolve", {"decision": "approved", "admin_note": "ok", "lock_rating": "on"})
with app.app_context():
    check("appeal resolved + rating locked", db.session.get(step.RatingAppeal, ap).status == "approved" and db.session.get(A, I["a3"]).rating_locked)
post("/edit-profile", {"name": "Admin Two", "email": "admin@step.ie"})
with app.app_context():
    check("admin edit_profile saved name", db.session.get(U, I["admin"]).name == "Admin Two")
post("/admin/universities", {"name": "Ulster University", "domain": "ulster.ac.uk"})
post("/admin/universities", {"name": "Dup", "domain": "ucc.ie"})
with app.app_context():
    check("admin added university, duplicate rejected", step.University.query.count() == 2)
    ulster = step.University.query.filter_by(domain="ulster.ac.uk").first().id
post(f"/admin/universities/{ulster}/delete")
with app.app_context():
    check("admin deleted university", step.University.query.count() == 1)
post(f"/admin/users/{I['bob']}/delete")
with app.app_context():
    check("admin delete user with applications actually deletes", db.session.get(U, I["bob"]) is None and A.query.filter_by(student_id=I["bob"]).count() == 0)
get("/admin/users")

_real_print("\n=== FORMS: university ===")
login("careers@ucc.ie")
post("/edit-profile", {"name": "UCC Careers Team", "email": "careers@ucc.ie", "department": "Computer Science"})
with app.app_context():
    s = db.session.get(U, I["staff"]); check("university edit_profile saved name/department", s.name == "UCC Careers Team" and s.department == "Computer Science", (s.name, s.department))

_real_print("\n=== REGISTER variants ===")
client.get("/logout")
post("/register", {"name": "Co2", "email": "co2@newco.ie", "password": "x", "role": "company", "company_size": "startup", "website": "https://newco.ie", "location": "Belfast", "bio": "b",
                   "logo": (io.BytesIO(b"\x89PNG"), "l.png")}, content_type="multipart/form-data")
with app.app_context():
    u = U.query.filter_by(email="co2@newco.ie").first()
    check("register company saved size/website/location/logo", u and (u.company_size, u.website, u.location) == ("startup", "https://newco.ie", "Belfast") and u.logo, (u.company_size, u.website, u.location, u.logo) if u else None)
post("/register", {"name": "S2", "email": "s2@ucc.ie", "password": "x", "role": "student", "skills": "Java", "grades": "2:1", "projects": "p", "references": "Dr Who\nProf Ann, Dr Bee"})
with app.app_context():
    u = U.query.filter_by(email="s2@ucc.ie").first()
    check("register student auto-linked to university by domain", u.university_id == I["uni"], f"university_id={u.university_id}")
    check("register student references saved (3)", step.LecturerReference.query.filter_by(student_id=u.id).count() == 3, step.LecturerReference.query.filter_by(student_id=u.id).count())
post("/register", {"name": "Staff2", "email": "dean@ucc.ie", "password": "x", "role": "university", "department": "Engineering"})
with app.app_context():
    u = U.query.filter_by(email="dean@ucc.ie").first(); check("register university staff linked by domain + department", u.university_id == I["uni"] and u.department == "Engineering")
post("/register", {"name": "Adm", "email": "a2@step.ie", "password": "x", "role": "admin", "admin_code": "adm"})
post("/register", {"name": "Adm", "email": "a3@step.ie", "password": "x", "role": "admin", "admin_code": "wrong"}, expect=(200,))
login("s2@ucc.ie", "x"); get("/student"); get("/student/portfolio/edit"); get("/edit-profile")
html = client.get("/student").get_data(as_text=True)
check("students can reach account settings from the navbar", 'href="/edit-profile"' in html)
check("dashboard checklist ticks the lecturer references entered at registration",
      'text-decoration-line-through text-muted">Add a lecturer reference' in html)
check("dashboard checklist leaves portfolio media open until something is uploaded",
      'text-decoration-line-through text-muted">Upload portfolio media' not in html and "Upload portfolio media" in html)

_real_print("\n=== UNDEFINED TEMPLATE REFERENCES ===")
for t in sorted(UNDEF):
    attrs = sorted(k for k in UNDEF[t] if not k.startswith("{{"))
    tops = sorted(k for k in UNDEF[t] if k.startswith("{{"))
    if attrs: _real_print(f"  {t}: ATTR {attrs}")
    if tops: _real_print(f"  {t}: ctx  {tops}")

_real_print("\n=== SUMMARY ===")
if BAD:
    for b in BAD: _real_print("  ", b)
    _real_print(f"FAILED: {len(BAD)} problem(s)")
else:
    _real_print("ALL PAGES AND FORMS OK")
