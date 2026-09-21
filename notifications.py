"""
Notification orchestration for STEP
===================================

Decides WHO is told WHAT when a domain event happens, then fans out to the
channels: email (email_service), the in-app Notification table, and the
real-time SSE broker. Routes call one function per event and never build
emails themselves.

Every function is safe to call from inside a request handler: failures are
logged, the database session is rolled back if needed, and nothing is raised.

Events
------
    notify_welcome(user)                                   account created
    notify_task_posted(task)                               company posted a task
    notify_selection_decision(selected_app, rejected_apps) company chose a student
    notify_application_rejected(application, reason)       company declined one applicant

Relevance for task alerts
-------------------------
A student is "relevant" to a task when they share at least one skill tag with
it (recommender.match_student_to_task, which also mines free text such as the
task requirements and the student's project descriptions). Results are ranked
by similarity and capped by EMAIL_TASK_ALERT_MAX_RECIPIENTS. Optionally raise
EMAIL_TASK_ALERT_MIN_MATCH (0..1 Jaccard score) to tighten the filter.

app is imported lazily inside functions (same pattern as recommender.py) to
avoid a circular import.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Iterable, List, Sequence, Tuple

import email_service as emails

log = logging.getLogger("step.notifications")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _in_app(user_id: int, message: str, task_id=None) -> None:
    """Add (not commit) an in-app Notification row."""
    from app import db, Notification

    db.session.add(Notification(user_id=user_id, task_id=task_id, message=message[:255]))


def _publish(user_id: int, event: dict) -> None:
    """Push a real-time SSE event to a connected browser, if any."""
    try:
        from app import broker

        broker.publish(user_id, event)
    except Exception:  # noqa: BLE001 - real-time is best effort
        pass


def _commit_quietly() -> None:
    from app import db

    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        log.exception("[notify] commit failed; rolling back")
        db.session.rollback()


# ============================================================================
# Welcome
# ============================================================================

def notify_welcome(user) -> bool:
    """Send the welcome email right after a successful registration."""
    try:
        return emails.send_welcome_email(user)
    except Exception:  # noqa: BLE001
        log.exception("[notify] welcome email failed for user %s", getattr(user, "id", "?"))
        return False


# ============================================================================
# Task posted
# ============================================================================

def find_relevant_students(task, *, min_score: float | None = None, limit: int | None = None) -> List[Tuple[object, float, List[str]]]:
    """Return [(student, similarity, matched_skills)] ranked best-first."""
    from app import User
    from recommender import match_student_to_task

    if min_score is None:
        min_score = _env_float("EMAIL_TASK_ALERT_MIN_MATCH", 0.0)
    if limit is None:
        limit = _env_int("EMAIL_TASK_ALERT_MAX_RECIPIENTS", 100)

    ranked: List[Tuple[object, float, List[str]]] = []
    for student in User.query.filter_by(role="student").all():
        if not student.email or student.id == getattr(task, "company_id", None):
            continue
        score, matched = match_student_to_task(student, task)
        if matched and score >= min_score:
            ranked.append((student, score, matched))

    ranked.sort(key=lambda item: (-item[1], item[0].id))
    return ranked[: max(0, limit)]


def notify_task_posted(task) -> int:
    """Email + in-app alert every student whose skills match the task.

    Returns the number of students alerted (0 when no one matched or on error).
    """
    try:
        matches = find_relevant_students(task)
        if not matches:
            log.info("[notify] task %s: no students matched its skill tags", task.id)
            return 0

        company = task.company
        for student, score, matched in matches:
            emails.send_task_posted_notification(task, company, student, matched_skills=matched)
            _in_app(student.id, f"New task matching your skills: {task.title}", task_id=task.id)
            _publish(student.id, {
                "type": "task_posted",
                "title": "New task matches your skills",
                "message": task.title,
                "task_id": task.id,
            })
        _commit_quietly()
        log.info("[notify] task %s: alerted %d student(s)", task.id, len(matches))
        return len(matches)
    except Exception:  # noqa: BLE001
        log.exception("[notify] task-posted alerts failed for task %s", getattr(task, "id", "?"))
        try:
            from app import db

            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


# ============================================================================
# Selection decisions
# ============================================================================

def notify_selection_decision(selected_application, rejected_applications: Iterable = ()) -> Dict[str, int]:
    """Email the selected student and everyone who was passed over.

    The route is expected to have committed the status changes already; this
    function only adds in-app rows for the not-selected students and commits.
    Returns {"selected": n, "not_selected": n}.
    """
    counts = {"selected": 0, "not_selected": 0}
    try:
        task = selected_application.task
        winner = selected_application.student
        if emails.send_application_accepted_notification(selected_application, task, winner):
            counts["selected"] += 1
        _publish(winner.id, {
            "type": "application_selected",
            "title": "You have been selected",
            "message": task.title,
            "task_id": task.id,
        })

        for application in rejected_applications:
            student = application.student
            if student is None or student.id == winner.id:
                continue
            if emails.send_application_rejected_notification(application, task, student):
                counts["not_selected"] += 1
            _in_app(student.id, f"Another applicant was selected for: {task.title}", task_id=task.id)
            _publish(student.id, {
                "type": "application_not_selected",
                "title": "Application update",
                "message": task.title,
                "task_id": task.id,
            })
        _commit_quietly()
    except Exception:  # noqa: BLE001
        log.exception("[notify] selection emails failed for application %s", getattr(selected_application, "id", "?"))
    return counts


def notify_application_rejected(application, reason: str = "") -> bool:
    """A company declined one application without (yet) selecting anyone."""
    try:
        task = application.task
        student = application.student
        sent = emails.send_application_rejected_notification(application, task, student, reason=reason)
        _in_app(student.id, f"Your application was not selected for: {task.title}", task_id=task.id)
        _publish(student.id, {
            "type": "application_not_selected",
            "title": "Application update",
            "message": task.title,
            "task_id": task.id,
        })
        _commit_quietly()
        return sent
    except Exception:  # noqa: BLE001
        log.exception("[notify] rejection email failed for application %s", getattr(application, "id", "?"))
        return False
