# Outgoing email. Uses SMTP when MAIL_SERVER is configured; otherwise the
# message is written to the log so password-reset links still work in
# development. In tests, messages are collected in app.extensions["outbox"].

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger(__name__)


def configure_mail(app) -> None:
    import os

    env = os.environ
    defaults = {
        "MAIL_SERVER": env.get("MAIL_SERVER"),
        "MAIL_PORT": int(env.get("MAIL_PORT", "587")),
        "MAIL_USERNAME": env.get("MAIL_USERNAME"),
        "MAIL_PASSWORD": env.get("MAIL_PASSWORD"),
        "MAIL_USE_TLS": env.get("MAIL_USE_TLS", "1").lower() in ("1", "true", "yes"),
        "MAIL_USE_SSL": env.get("MAIL_USE_SSL", "0").lower() in ("1", "true", "yes"),
        "MAIL_DEFAULT_SENDER": env.get("MAIL_DEFAULT_SENDER", "STEP <no-reply@step.local>"),
        # Send emails for marketplace events (selection, submissions, reviews)
        "MAIL_EVENT_EMAILS": env.get("MAIL_EVENT_EMAILS", "1").lower() in ("1", "true", "yes"),
    }
    for key, value in defaults.items():
        app.config.setdefault(key, value)
    app.extensions.setdefault("outbox", [])


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Never raises: returns False and logs on failure."""
    app = current_app
    msg = EmailMessage()
    msg["From"] = app.config["MAIL_DEFAULT_SENDER"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if app.config.get("TESTING"):
        app.extensions["outbox"].append(msg)
        return True

    server = app.config.get("MAIL_SERVER")
    if not server:
        log.warning("MAIL_SERVER not set; email to %s not sent.\nSubject: %s\n\n%s", to, subject, body)
        return False

    try:
        smtp_cls = smtplib.SMTP_SSL if app.config["MAIL_USE_SSL"] else smtplib.SMTP
        with smtp_cls(server, app.config["MAIL_PORT"], timeout=10) as smtp:
            if app.config["MAIL_USE_TLS"] and not app.config["MAIL_USE_SSL"]:
                smtp.starttls()
            if app.config.get("MAIL_USERNAME"):
                smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"] or "")
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        log.exception("Failed to send email to %s (%s)", to, subject)
        return False
