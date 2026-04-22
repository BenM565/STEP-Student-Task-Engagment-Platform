"""
PHASE 2 — Skill-based Task Recommendations

Pure helper module for computing Jaccard similarity between student skills
and task tags to power personalized task recommendations.

Uses comma-separated text fields from existing schema:
- User.skills (e.g., "Python, Flask, SQL")
- Task.tags (e.g., "web development, backend, database")

No database writes - only reads and computation.

FIX (v1.1): Replaced deprecated User.query.get() / Task.query.get() calls
with db.session.get() for SQLAlchemy 2.0 compatibility.
"""

from typing import List, Dict, Tuple


def _parse_csv(text: str) -> set:
    if not text:
        return set()
    return {t.strip().lower() for t in text.split(",") if t.strip()}


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def recommend_tasks_for_student(student_id: int, limit: int = 5) -> List[Dict]:
    from app import db, User, Task, Application  # local import avoids circular dependency

    # FIX: use db.session.get() instead of deprecated User.query.get()
    student = db.session.get(User, student_id)
    if not student or student.role.lower() != "student":
        return []

    student_skills = _parse_csv(student.skills)
    if not student_skills:
        return []

    applied_task_ids = {
        app.task_id for app in Application.query.filter_by(student_id=student_id).all()
    }

    open_tasks = Task.query.filter(
        Task.status == "open",
        Task.tags.isnot(None),
        Task.tags != "",
        ~Task.id.in_(applied_task_ids)
    ).all()

    scored_tasks = []
    for task in open_tasks:
        task_tags = _parse_csv(task.tags)
        if not task_tags:
            continue

        similarity = _jaccard_similarity(student_skills, task_tags)
        if similarity > 0:
            # FIX: use db.session.get() instead of deprecated User.query.get()
            company = db.session.get(User, task.company_id)
            scored_tasks.append({
                "task_id": task.id,
                "title": task.title,
                "tags": task.tags,
                "similarity": similarity,
                "company_name": company.name if company else "Unknown"
            })

    scored_tasks.sort(key=lambda x: (-x["similarity"], -x["task_id"]))
    return scored_tasks[:limit]


def recommend_students_for_task(task_id: int, limit: int = 10) -> List[Dict]:
    from app import db, User, Task, Application

    # FIX: use db.session.get() instead of deprecated Task.query.get()
    task = db.session.get(Task, task_id)
    if not task:
        return []

    task_tags = _parse_csv(task.tags)
    if not task_tags:
        return []

    applications = Application.query.filter_by(task_id=task_id).all()

    scored_students = []
    for app in applications:
        # FIX: use db.session.get() instead of deprecated User.query.get()
        student = db.session.get(User, app.student_id)
        if not student or not student.skills:
            continue

        student_skills = _parse_csv(student.skills)
        if not student_skills:
            continue

        similarity = _jaccard_similarity(student_skills, task_tags)
        scored_students.append({
            "student_id": student.id,
            "name": student.name,
            "skills": student.skills,
            "similarity": similarity,
            "application_id": app.id
        })

    scored_students.sort(key=lambda x: (-x["similarity"], -x["application_id"]))
    return scored_students[:limit]


def get_skill_overlap(student_id: int, task_id: int) -> Tuple[float, List[str], List[str]]:
    from app import db, User, Task

    # FIX: use db.session.get() instead of deprecated .query.get()
    student = db.session.get(User, student_id)
    task = db.session.get(Task, task_id)

    if not student or not task:
        return (0.0, [], [])

    student_skills = _parse_csv(student.skills)
    task_tags = _parse_csv(task.tags)

    if not student_skills or not task_tags:
        return (0.0, [], [])

    similarity = _jaccard_similarity(student_skills, task_tags)
    matching = sorted(student_skills & task_tags)
    missing = sorted(task_tags - student_skills)

    return (similarity, matching, missing)
