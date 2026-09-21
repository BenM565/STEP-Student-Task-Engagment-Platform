"""
Email service for STEP
======================

Provider-agnostic transactional email with three interchangeable back-ends,
selected with one environment variable:

    EMAIL_PROVIDER=resend   HTTPS API (api.resend.com). Recommended on Render:
                            free web services block outbound SMTP ports
                            25/465/587, HTTPS on 443 is unaffected.
    EMAIL_PROVIDER=smtp     Any SMTP relay: a Gmail app password for local
                            development, SendGrid / Mailgun / Brevo SMTP, or an
                            on-premises relay inside an enterprise network.
    EMAIL_PROVIDER=console  Logs the message instead of sending. Default when
                            nothing is configured, so the app always boots and
                            no request ever fails because of email.

Design rules
------------
* Nothing is hardcoded. Every credential, sender and base URL comes from the
  environment (see .env.example). Secrets never live in source control.
* Sending is non-blocking. Messages go to a small thread pool so a slow mail
  server never slows a request. EMAIL_SYNC=1 sends inline (used by tests).
* Sending never raises into a request. Failures are logged with enough detail
  to debug; the calling code carries on.
* All user-supplied text is HTML-escaped before it is placed in a template.
* Every message is built by one `_compose()` layout, so all emails share the
  same look, plain-text fallback, and footer.

Public API (names are stable; app.py and notifications.py depend on them)
-------------------------------------------------------------------------
    send_email(...)                             low-level send
    send_welcome_email(user)
    send_task_posted_notification(task, company, student, matched_skills)
    send_application_received_notification(application, task, company)
    send_application_accepted_notification(application, task, student)
    send_application_rejected_notification(application, task, student, reason)
    send_work_submitted_notification(application, task, company)
    send_work_approved_notification(application, task, student)
    send_change_requested_notification(application, task, student, feedback)
    send_dispute_notification(dispute, admin_email)
    get_provider() / reset_provider() / describe_provider()
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from flask import current_app, has_app_context

log = logging.getLogger("step.email")

DEFAULT_APP_URL = "http://localhost:5000"
DEFAULT_SENDER = "STEP Platform <no-reply@step.local>"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ============================================================================
# Message + provider abstraction
# ============================================================================

class EmailDeliveryError(RuntimeError):
    """Raised by a provider when the upstream service rejects a message."""


@dataclass
class OutboundEmail:
    to: str
    subject: str
    html: str
    text: str
    sender: str
    reply_to: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)


class EmailProvider:
    """Interface every back-end implements. `send` raises on failure."""

    name = "base"

    def send(self, message: OutboundEmail) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleProvider(EmailProvider):
    """Logs messages and keeps the last few in memory (handy for tests/demos)."""

    name = "console"
    KEEP = 200

    def __init__(self) -> None:
        self.sent: List[OutboundEmail] = []

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)
        if len(self.sent) > self.KEEP:
            del self.sent[: len(self.sent) - self.KEEP]
        log.info("[email:console] to=%s subject=%r", message.to, message.subject)
        print(f"[STEP email] to={message.to} subject={message.subject!r}")
        print(message.text.strip())


class SMTPProvider(EmailProvider):
    """Standard-library SMTP client. Works with any relay that speaks STARTTLS/SSL."""

    name = "smtp"

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout: int = 20,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.timeout = timeout

    def send(self, message: OutboundEmail) -> None:
        em = EmailMessage()
        em["From"] = message.sender
        em["To"] = message.to
        em["Subject"] = message.subject
        if message.reply_to:
            em["Reply-To"] = message.reply_to
        em.set_content(message.text)
        em.add_alternative(message.html, subtype="html")

        context = ssl.create_default_context()
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context) as smtp:
                if self.username:
                    smtp.login(self.username, self.password or "")
                smtp.send_message(em)
            return

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.ehlo()
            if self.use_tls:
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(em)


class ResendProvider(EmailProvider):
    """Resend HTTPS API (https://resend.com/docs/api-reference/emails/send-email).

    Uses only the standard library so no extra dependency is needed.
    """

    name = "resend"
    ENDPOINT = "https://api.resend.com/emails"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def _tag(value: str) -> str:
        # Resend tags allow ASCII letters, numbers, underscores and dashes only
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(value))[:256] or "none"

    def send(self, message: OutboundEmail) -> None:
        payload: Dict[str, object] = {
            "from": message.sender,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        if message.tags:
            payload["tags"] = [
                {"name": self._tag(k), "value": self._tag(v)} for k, v in message.tags.items()
            ]

        request = urllib.request.Request(
            self.ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "STEP-Platform/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise EmailDeliveryError(f"Resend rejected the message (HTTP {exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmailDeliveryError(f"Could not reach Resend: {exc.reason}") from exc

        log.info("[email:resend] accepted id=%s to=%s", body.get("id"), message.to)


# ============================================================================
# Provider selection (environment driven, built once, thread-safe)
# ============================================================================

_provider: Optional[EmailProvider] = None
_provider_lock = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _build_provider() -> EmailProvider:
    choice = _env("EMAIL_PROVIDER").lower()
    if not choice:
        if _env("RESEND_API_KEY"):
            choice = "resend"
        elif _env("MAIL_SERVER") and _env("MAIL_USERNAME"):
            choice = "smtp"
        else:
            choice = "console"

    if choice == "resend":
        api_key = _env("RESEND_API_KEY")
        if not api_key:
            log.warning("EMAIL_PROVIDER=resend but RESEND_API_KEY is empty; using console provider")
            return ConsoleProvider()
        return ResendProvider(api_key)

    if choice == "smtp":
        host = _env("MAIL_SERVER")
        if not host:
            log.warning("EMAIL_PROVIDER=smtp but MAIL_SERVER is empty; using console provider")
            return ConsoleProvider()
        try:
            port = int(_env("MAIL_PORT", "587"))
        except ValueError:
            port = 587
        return SMTPProvider(
            host=host,
            port=port,
            username=_env("MAIL_USERNAME") or None,
            password=os.getenv("MAIL_PASSWORD") or None,
            use_tls=_env_bool("MAIL_USE_TLS", True),
            use_ssl=_env_bool("MAIL_USE_SSL", False),
        )

    if choice != "console":
        log.warning("Unknown EMAIL_PROVIDER=%r; using console provider", choice)
    return ConsoleProvider()


def get_provider() -> EmailProvider:
    """Return the process-wide provider, building it on first use."""
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = _build_provider()
    return _provider


def reset_provider() -> None:
    """Forget the cached provider so the next send re-reads the environment."""
    global _provider
    with _provider_lock:
        _provider = None


def describe_provider() -> str:
    """One-line description for startup logs (never includes secrets)."""
    provider = get_provider()
    if isinstance(provider, ResendProvider):
        return "resend (HTTPS API)"
    if isinstance(provider, SMTPProvider):
        return f"smtp ({provider.host}:{provider.port})"
    return "console (emails are logged, not delivered)"


# ============================================================================
# Sending
# ============================================================================

def _worker_count() -> int:
    try:
        return max(1, int(_env("EMAIL_WORKERS", "2")))
    except ValueError:
        return 2


_executor = ThreadPoolExecutor(max_workers=_worker_count(), thread_name_prefix="step-email")


def _clean_address(value: Optional[str]) -> str:
    _, address = parseaddr(value or "")
    address = address.strip()
    return address if _EMAIL_RE.match(address) else ""


def default_sender() -> str:
    for name in ("EMAIL_FROM", "MAIL_DEFAULT_SENDER"):
        value = _env(name)
        if value:
            return value
    username = _env("MAIL_USERNAME")
    if _clean_address(username):
        return f"STEP Platform <{username}>"
    return DEFAULT_SENDER


def app_url(path: str = "") -> str:
    """Absolute URL into the app, from APP_URL (env) or Flask config."""
    base = _env("APP_URL")
    if not base and has_app_context():
        base = (current_app.config.get("APP_URL") or "").strip()
    base = (base or DEFAULT_APP_URL).rstrip("/")
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def _deliver(message: OutboundEmail) -> bool:
    try:
        get_provider().send(message)
        return True
    except Exception as exc:  # noqa: BLE001 - never let email break a request
        log.error("[email] delivery failed to=%s subject=%r: %s", message.to, message.subject, exc)
        return False


def send_email(
    recipient_email: Optional[str],
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    sender: Optional[str] = None,
    reply_to: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
) -> bool:
    """Queue one email. Returns True when accepted for delivery.

    With EMAIL_SYNC=1 the message is sent inline and the return value reflects
    the real delivery result (used by tests and one-off scripts).
    """
    to = _clean_address(recipient_email)
    if not to:
        log.warning("[email] skipped %r: no valid recipient (%r)", subject, recipient_email)
        return False

    message = OutboundEmail(
        to=to,
        subject=(subject or "").strip() or "STEP Platform",
        html=html_body,
        text=text_body or "Please open this email in an HTML-capable client.",
        sender=sender or default_sender(),
        reply_to=reply_to or (_env("EMAIL_REPLY_TO") or None),
        tags=dict(tags or {}),
    )

    if _env_bool("EMAIL_SYNC", False):
        return _deliver(message)

    _executor.submit(_deliver, message)
    return True


# ============================================================================
# Shared layout
# ============================================================================

def _compose(
    *,
    title: str,
    greeting: str,
    paragraphs: Sequence[str],
    details: Optional[Iterable[Tuple[str, str]]] = None,
    cta_label: Optional[str] = None,
    cta_url: Optional[str] = None,
    closing: str = "The STEP team",
) -> Tuple[str, str]:
    """Build (html, text) for one message. All dynamic text is escaped here."""
    e = html.escape
    rows = [(label, str(value)) for label, value in (details or []) if value not in (None, "")]

    html_paragraphs = "".join(
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.55;">{e(p)}</p>' for p in paragraphs
    )
    html_details = ""
    if rows:
        cells = "".join(
            '<tr>'
            f'<td style="padding:8px 12px 8px 0;color:#666;font-size:14px;white-space:nowrap;vertical-align:top;">{e(label)}</td>'
            f'<td style="padding:8px 0;font-size:14px;vertical-align:top;">{e(value)}</td>'
            '</tr>'
            for label, value in rows
        )
        html_details = (
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;margin:8px 0 20px;border-top:1px solid #e5e5e5;border-bottom:1px solid #e5e5e5;">'
            f"{cells}</table>"
        )
    html_cta = ""
    if cta_label and cta_url:
        html_cta = (
            f'<p style="margin:8px 0 24px;"><a href="{e(cta_url)}" '
            'style="display:inline-block;background:#111;color:#fff;padding:12px 22px;'
            'text-decoration:none;font-weight:600;font-size:15px;border-radius:4px;">'
            f"{e(cta_label)}</a></p>"
        )

    html_body = f"""<!doctype html>
<html lang="en">
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#111;">
  <div style="max-width:600px;margin:0 auto;padding:32px 16px;">
    <div style="font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#666;margin-bottom:16px;">STEP &middot; Student Task Engagement Platform</div>
    <div style="background:#fff;border:1px solid #e5e5e5;padding:32px;">
      <h1 style="font-size:22px;line-height:1.3;margin:0 0 20px;font-weight:600;">{e(title)}</h1>
      <p style="margin:0 0 16px;font-size:16px;line-height:1.55;">{e(greeting)}</p>
      {html_paragraphs}
      {html_details}
      {html_cta}
      <p style="margin:0;color:#666;font-size:14px;">{e(closing)}</p>
    </div>
    <div style="font-size:12px;color:#888;margin-top:16px;line-height:1.5;">
      You are receiving this because you have a STEP account. <a href="{e(app_url())}" style="color:#888;">{e(app_url())}</a>
    </div>
  </div>
</body>
</html>"""

    text_lines: List[str] = [title, "", greeting, ""]
    for p in paragraphs:
        text_lines += [p, ""]
    if rows:
        text_lines += [f"{label}: {value}" for label, value in rows]
        text_lines.append("")
    if cta_label and cta_url:
        text_lines += [f"{cta_label}: {cta_url}", ""]
    text_lines += [closing, "", f"STEP Platform - {app_url()}"]
    return html_body, "\n".join(text_lines)


# ============================================================================
# Small formatting helpers
# ============================================================================

def _first_name(user) -> str:
    name = (getattr(user, "name", "") or "").strip()
    return name.split(" ")[0] if name else "there"


def _display_name(user, fallback: str = "the company") -> str:
    return (getattr(user, "name", "") or "").strip() or fallback


def _money(value) -> str:
    try:
        return f"€{float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


def _task_payment(task) -> str:
    payment_type = (getattr(task, "payment_type", "") or "fixed").lower()
    if payment_type == "hourly" and getattr(task, "hourly_rate", None):
        return f"{_money(task.hourly_rate)} per hour"
    if getattr(task, "fixed_price", None):
        return f"{_money(task.fixed_price)} fixed price"
    return "To be agreed"


def _hours(task) -> str:
    hours = getattr(task, "estimated_hours", None)
    return f"{hours} hours" if hours else ""


def _when(value) -> str:
    return value.strftime("%d %b %Y, %H:%M") if value else ""


def _platform_fee_percent() -> float:
    if has_app_context():
        try:
            return float(current_app.config.get("PLATFORM_FEE_PERCENT", 10))
        except (TypeError, ValueError):
            pass
    try:
        return float(_env("PLATFORM_FEE_PERCENT", "10"))
    except ValueError:
        return 10.0


# ============================================================================
# Transactional messages
# ============================================================================

_ONBOARDING = {
    "student": (
        [
            "Your account is ready. STEP connects you with real, paid tasks from companies "
            "so you can build a portfolio of verified work while you study.",
            "Start by adding your skills and a project or two. Companies see this when they "
            "review applications, and it powers the task matches we email you about.",
        ],
        "Complete your portfolio",
        "/student/portfolio/edit",
    ),
    "company": (
        [
            "Your account is ready. Post a task, and STEP alerts students whose skills match, "
            "collects applications, and handles selection, submission and review in one place.",
            "A clear description and a few skill tags get the best matches.",
        ],
        "Post your first task",
        "/add-task",
    ),
    "university": (
        [
            "Your account is ready. Your dashboard shows how students from your institution "
            "are engaging with industry tasks: applications, selections, completions and ratings.",
        ],
        "Open your dashboard",
        "/university/dashboard",
    ),
    "admin": (
        [
            "Your administrator account is ready. From the admin console you can verify "
            "students, review disputes and oversee platform activity.",
        ],
        "Open the admin console",
        "/admin",
    ),
}


def send_welcome_email(user) -> bool:
    """Welcome message sent once, immediately after successful registration."""
    role = (getattr(user, "role", "") or "student").lower()
    paragraphs, cta_label, cta_path = _ONBOARDING.get(role, _ONBOARDING["student"])
    html_body, text_body = _compose(
        title="Welcome to STEP",
        greeting=f"Hi {_first_name(user)},",
        paragraphs=paragraphs,
        details=[("Account", user.email), ("Role", role.capitalize())],
        cta_label=cta_label,
        cta_url=app_url(cta_path),
    )
    return send_email(
        user.email,
        f"Welcome to STEP, {_first_name(user)}",
        html_body,
        text_body,
        tags={"event": "welcome", "role": role},
    )


def send_task_posted_notification(task, company, student, matched_skills: Optional[Sequence[str]] = None) -> bool:
    """Alert one student that a task matching their skills was just posted."""
    description = (getattr(task, "description", "") or "").strip()
    if len(description) > 240:
        description = description[:237].rstrip() + "..."
    matched = ", ".join(matched_skills) if matched_skills else ""

    paragraphs = [
        f"{_display_name(company)} just posted a task that matches your profile.",
    ]
    if description:
        paragraphs.append(description)

    html_body, text_body = _compose(
        title=task.title,
        greeting=f"Hi {_first_name(student)},",
        paragraphs=paragraphs,
        details=[
            ("Posted by", _display_name(company)),
            ("Skills matched", matched),
            ("Estimated effort", _hours(task)),
            ("Payment", _task_payment(task)),
        ],
        cta_label="View task and apply",
        cta_url=app_url(f"/task/{task.id}"),
    )
    return send_email(
        student.email,
        f"New task matches your skills: {task.title}",
        html_body,
        text_body,
        tags={"event": "task_posted", "task": str(task.id)},
    )


def send_application_received_notification(application, task, company) -> bool:
    """Tell the company a student has applied."""
    student = application.student
    html_body, text_body = _compose(
        title="New application received",
        greeting=f"Hi {_first_name(company)},",
        paragraphs=[f"{_display_name(student, 'A student')} has applied for \"{task.title}\"."],
        details=[
            ("Applicant", _display_name(student, "")),
            ("Skills", getattr(student, "skills", "") or "Not specified"),
            ("Applied", _when(getattr(application, "created_at", None))),
        ],
        cta_label="Review applicants",
        cta_url=app_url(f"/company/applicants/{task.id}"),
    )
    return send_email(
        company.email,
        f"New application: {task.title}",
        html_body,
        text_body,
        tags={"event": "application_received", "task": str(task.id)},
    )


def send_application_accepted_notification(application, task, student) -> bool:
    """Selected: the company chose this student for the task."""
    company = getattr(task, "company", None)
    html_body, text_body = _compose(
        title="You have been selected",
        greeting=f"Hi {_first_name(student)},",
        paragraphs=[
            f"{_display_name(company)} has selected you for \"{task.title}\".",
            "Open the task to confirm, review the requirements, and upload your work when it is ready. "
            "The company reviews your submission on the platform and you are notified of the outcome.",
        ],
        details=[
            ("Company", _display_name(company)),
            ("Estimated effort", _hours(task)),
            ("Payment", _task_payment(task)),
        ],
        cta_label="Open the task",
        cta_url=app_url(f"/task/{task.id}"),
    )
    return send_email(
        student.email,
        f"You've been selected: {task.title}",
        html_body,
        text_body,
        tags={"event": "application_selected", "task": str(task.id)},
    )


def send_application_rejected_notification(application, task, student, reason: str = "") -> bool:
    """Not selected: the company chose someone else, or declined the application."""
    company = getattr(task, "company", None)
    paragraphs = [
        f"Thank you for applying for \"{task.title}\" with {_display_name(company)}. "
        "On this occasion another applicant was selected.",
        "Every application is seen by a real company, and new tasks are posted regularly. "
        "Keeping your skills and portfolio up to date improves your match on the next one.",
    ]
    details = [("Company", _display_name(company))]
    if reason:
        details.append(("Feedback", reason))
    html_body, text_body = _compose(
        title="Update on your application",
        greeting=f"Hi {_first_name(student)},",
        paragraphs=paragraphs,
        details=details,
        cta_label="Browse open tasks",
        cta_url=app_url("/browse-tasks"),
    )
    return send_email(
        student.email,
        f"Update on your application: {task.title}",
        html_body,
        text_body,
        tags={"event": "application_not_selected", "task": str(task.id)},
    )


def send_work_submitted_notification(application, task, company) -> bool:
    """Tell the company the selected student has uploaded work for review."""
    student = application.student
    html_body, text_body = _compose(
        title="Work submitted for review",
        greeting=f"Hi {_first_name(company)},",
        paragraphs=[
            f"{_display_name(student, 'The student')} has submitted work for \"{task.title}\". "
            "Please review it and either approve it or request changes.",
        ],
        details=[
            ("Student", _display_name(student, "")),
            ("Submitted", _when(getattr(application, "submitted_at", None))),
        ],
        cta_label="Review submission",
        cta_url=app_url(f"/company/applicants/{task.id}"),
    )
    return send_email(
        company.email,
        f"Work submitted: {task.title}",
        html_body,
        text_body,
        tags={"event": "work_submitted", "task": str(task.id)},
    )


def send_work_approved_notification(application, task, student) -> bool:
    """Tell the student their work was approved (with the payment breakdown when priced)."""
    company = getattr(task, "company", None)
    details = [
        ("Company", _display_name(company)),
        ("Completed", _when(getattr(application, "completed_at", None))),
    ]
    # Only describe a payment that actually happened (Stripe escrow captured)
    if getattr(task, "fixed_price", None) and getattr(application, "payment_status", "") == "captured":
        fee_pct = _platform_fee_percent()
        fee = float(task.fixed_price) * fee_pct / 100.0
        details += [
            ("Task value", _money(task.fixed_price)),
            (f"Platform fee ({fee_pct:g}%)", f"-{_money(fee)}"),
            ("Paid to you", _money(float(task.fixed_price) - fee)),
        ]
    html_body, text_body = _compose(
        title="Your work has been approved",
        greeting=f"Hi {_first_name(student)},",
        paragraphs=[
            f"{_display_name(company)} approved your submission for \"{task.title}\". "
            "This task now appears as completed on your portfolio.",
        ],
        details=details,
        cta_label="View your dashboard",
        cta_url=app_url("/student"),
    )
    return send_email(
        student.email,
        f"Approved: {task.title}",
        html_body,
        text_body,
        tags={"event": "work_approved", "task": str(task.id)},
    )


def send_change_requested_notification(application, task, student, feedback: str = "") -> bool:
    """Tell the student the company wants revisions."""
    company = getattr(task, "company", None)
    html_body, text_body = _compose(
        title="Changes requested",
        greeting=f"Hi {_first_name(student)},",
        paragraphs=[
            f"{_display_name(company)} has reviewed your work on \"{task.title}\" and asked for changes. "
            "Please update your submission and upload it again.",
        ],
        details=[("Feedback", feedback or "See the task page for details")],
        cta_label="Open the task",
        cta_url=app_url(f"/task/{task.id}"),
    )
    return send_email(
        student.email,
        f"Changes requested: {task.title}",
        html_body,
        text_body,
        tags={"event": "changes_requested", "task": str(task.id)},
    )


def send_dispute_notification(dispute, admin_email: str) -> bool:
    """Tell an administrator a dispute needs review."""
    raised_by = getattr(dispute, "raised_by_user", None)
    against = getattr(dispute, "against_user", None)
    task = getattr(dispute, "task", None)
    html_body, text_body = _compose(
        title="New dispute filed",
        greeting="Hello,",
        paragraphs=["A dispute has been filed and needs an administrator's review."],
        details=[
            ("Raised by", _display_name(raised_by, "")),
            ("Against", _display_name(against, "") if against else ""),
            ("Task", getattr(task, "title", "") if task else ""),
            ("Severity", f"{getattr(dispute, 'severity', 1)}/5"),
            ("Message", (getattr(dispute, "message", "") or "")[:500]),
        ],
        cta_label="Review dispute",
        cta_url=app_url("/admin-disputes"),
        closing="STEP administration",
    )
    return send_email(
        admin_email,
        f"Dispute #{getattr(dispute, 'id', '')} needs review",
        html_body,
        text_body,
        tags={"event": "dispute_filed"},
    )
