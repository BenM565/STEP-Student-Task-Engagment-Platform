# STEP platform guide

How STEP works end to end, how to run it, and what each part of the code does. The AI agents have their own guide in [AI_AGENTS.md](AI_AGENTS.md).

## The workflow

```
Company posts a task (or an AI agent drafts it)
  → students find it and apply with a note
  → company reviews applicants' profiles, ratings and notes, then selects one
  → the student submits work (message, link, file)
  → company approves it or requests changes with feedback (repeat as needed)
  → company rates the student → the work and review appear on the student's portfolio
```

Task status moves `open → in_progress → completed`. A company can close an open task (`closed`) and reopen it later. Every step notifies the other side in the app (bell icon), and the important ones also send email: being selected, work submitted, changes requested, and work approved.

| Role | What they can do |
|---|---|
| Student | Find open tasks, apply or withdraw, see application status, submit work and revisions, see feedback and ratings, public portfolio, raise disputes |
| Company | Post/edit/close tasks with deadlines, review applicants, select or decline, review submissions, rate students, AI agents, raise disputes |
| Admin | Platform stats, verify or remove students, resolve disputes. Admins are created from the command line; nobody can sign up as admin. |

## Running STEP

```
pip install -r requirements.txt
cp .env.example .env            # set FLASK_SECRET, DATABASE_URL, PUBLIC_BASE_URL, ANTHROPIC_API_KEY
flask --app app init-db         # create tables, or upgrade an existing STEP database
flask --app app create-admin    # first admin account
python app.py                   # development server (also runs init-db)
```

For production, run the app under a WSGI server (for example gunicorn) and run AI agents with `AI_EXECUTION_MODE=worker` plus one or more `flask --app app ai-agents worker` processes (see AI_AGENTS.md).

**Upgrading an existing database:** `init-db` is safe to run against the database the original STEP code created. It adds the new tables and columns and keeps all data. Tasks that already had a selected student are marked in progress. This is covered by `tests/test_upgrade_from_original.py`.

## Accounts and security

- **Passwords:** at least 8 characters, hashed with Werkzeug. Changing a password requires the current one.
- **Forgot password:** emails a signed link that expires after an hour (`PASSWORD_RESET_MAX_AGE`) and works once, because it is tied to the current password hash. The response is the same whether or not the email exists, so it doesn't reveal who has an account. In production the link is built only from `PUBLIC_BASE_URL`. If that isn't set, no email is sent and an error is logged, which prevents password-reset poisoning through a forged `Host` header.
- **CSRF:** every form carries a session token, and the server rejects any POST without it.
- **Sessions:** `HttpOnly` and `SameSite=Lax` cookies. Pages send `nosniff`, `SAMEORIGIN` framing and strict referrer headers.
- **Redirects:** after login, `?next=` only accepts paths on this site.
- **Access control:** every company page checks ownership and returns 404 for other companies' tasks, applications and submissions. Submission files download only for the student who uploaded them, the task's company, and admins.
- **Uploads:** submission files are limited by type and size (`SUBMISSION_MAX_FILE_MB`), stored under random names outside `/static`, and always downloaded as attachments.
- **Weak secret warning:** STEP logs a warning at startup if `FLASK_SECRET` is missing or a well-known value.

## Email

`core/mailer.py` sends plain-text email over SMTP when `MAIL_SERVER` is set. Without it, emails are written to the server log, which is enough to use password reset in development. Sending failures are logged and never break a request.

## Code layout

```
app.py                 app setup, login/registration, dashboard, task board, task CRUD, disputes, admin, CLI
extensions.py          shared SQLAlchemy instance
core/
  models.py            User, Task, Application, Dispute (moved from app.py, same tables) + Submission, Review, Notification
  schema.py            additive schema upgrades for existing databases
  security.py          CSRF, password policy, safe redirects, role_required, security headers
  accounts.py          forgot/reset/change password
  mailer.py            SMTP email with log fallback
  notifications.py     in-app notifications and event emails
  marketplace.py       task detail, apply/withdraw, my work, submissions, review, rating, portfolio
ai_agents/             AI agents (see AI_AGENTS.md)
templates/             Jinja templates (Bootstrap 5)
tests/                 pytest suite
```

## Tests

```
pytest
```

The suite includes:

- A crawl that renders every page as each role and when logged out, against data in every task state.
- The full work loop end to end.
- Permission and isolation checks for every company and student action.
- The password-reset attack cases: enumeration, reuse, expiry, tampering and host poisoning.
- An upgrade from the original database schema.
- The AI agents.

## Known gaps

- `.env` with a database password and a weak `FLASK_SECRET` is committed to git. Rotate both and remove the file from tracking.
- No payments or escrow yet. The README describes a Stripe escrow flow, but it isn't implemented.
- No email verification at sign-up. Students are marked verified by the `.ie` / "student" email rule or by an admin.
- No login rate limiting. Put the app behind a proxy with rate limits, or add Flask-Limiter, before public launch.
- Notifications update on page load; there is no live push.
