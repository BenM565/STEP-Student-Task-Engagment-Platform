# Test setup: in-memory SQLite, isolated upload dir, and a fake AI provider
# injected at the AIProvider interface (the real Anthropic provider is tested
# separately with a mocked SDK client).

import os
import sys

import pytest

# Must be set before app.py is imported: it reads DATABASE_URL at import time
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as step_app  # noqa: E402
from ai_agents.providers import ProviderError, StructuredResult, Usage  # noqa: E402

CSRF = "test-csrf-token"


def sample_task_draft(**overrides):
    draft = {
        "title": "User research and redesign proposal for website onboarding",
        "description": "Interview recent sign-ups and map the onboarding funnel to find where users drop off.",
        "objectives": ["Identify the top three causes of drop-off"],
        "required_skills": ["User research", "Figma"],
        "deliverables": ["Research report", "Clickable prototype"],
        "milestones": [{"name": "Research plan", "description": "Interview guide agreed with the company"}],
        "acceptance_criteria": ["At least 6 interviews summarised"],
        "estimated_hours": 40,
        "suggested_disciplines": ["Business Information Systems"],
        "questions_for_company": ["Can the student access analytics data?"],
    }
    draft.update(overrides)
    return draft


def sample_ba_output(recommended=True):
    return {
        "summary": "Onboarding loses users at verification.",
        "problem_definition": {
            "statement": "New customers abandon sign-up before verifying.",
            "business_context": "Acquisition spend is wasted.",
            "objectives": ["Raise completion to 80%"],
            "in_scope": ["Web sign-up"],
            "out_of_scope": ["Mobile app"],
            "stakeholders": ["Marketing"],
        },
        "functional_requirements": [{"id": "FR-1", "description": "Allow social sign-in", "priority": "should"}],
        "non_functional_requirements": [{"id": "NFR-1", "category": "Accessibility", "description": "WCAG 2.2 AA"}],
        "user_stories": [{
            "id": "US-1", "role": "new customer", "goal": "to sign up in under two minutes",
            "benefit": "I can start using the product", "acceptance_criteria": ["Given... when... then..."],
        }],
        "risks": [{"description": "Low interview uptake", "likelihood": "medium", "impact": "high", "mitigation": "Offer vouchers"}],
        "assumptions": ["Analytics are reliable"],
        "clarification_questions": ["What is the current completion rate?"],
        "recommended_next_steps": [{"step": "Run user interviews", "owner": "student", "rationale": "Hands-on research"}],
        "human_task_assessment": {
            "recommended": recommended,
            "rationale": "Research needs a person.",
            "suggested_task": sample_task_draft() if recommended else None,
        },
    }


class FakeProvider:
    """Implements the AIProvider interface; returns canned structured output or raises."""

    name = "fake"

    def __init__(self):
        self.calls = []
        self.output = sample_ba_output()
        self.error = None

    def generate(self, **kwargs):  # pragma: no cover - not used by current agents
        raise NotImplementedError

    def structured_output(self, *, system, prompt, output_model, max_tokens=16000, effort=None):
        self.calls.append({"system": system, "prompt": prompt, "output_model": output_model,
                           "max_tokens": max_tokens, "effort": effort})
        if self.error:
            raise self.error
        return StructuredResult(output=output_model.model_validate(self.output), model="claude-opus-5-5",
                                provider=self.name, usage=Usage(input_tokens=1000, output_tokens=500))


@pytest.fixture()
def app(tmp_path):
    flask_app = step_app.app
    provider = FakeProvider()
    # app is a module-level singleton: restore config so tests cannot leak settings
    saved_config = dict(flask_app.config)
    flask_app.config.update(
        TESTING=True,
        AI_UPLOAD_DIR=str(tmp_path / "uploads"),
        AI_PROVIDER_INSTANCE=provider,
        AI_MAX_RUNS_PER_DAY=50,
        ANTHROPIC_API_KEY=None,
    )
    flask_app.fake_provider = provider
    with flask_app.app_context():
        step_app.db.create_all()
        yield flask_app
        step_app.db.session.remove()
        step_app.db.drop_all()
    flask_app.config.clear()
    flask_app.config.update(saved_config)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(app):
    def _make(role="company", email=None, name=None):
        email = email or f"{role}{_make.n}@example.com"
        _make.n += 1
        user = step_app.User(role=role, name=name or email.split("@")[0], email=email)
        user.set_password("pw")
        step_app.db.session.add(user)
        step_app.db.session.commit()
        return user
    _make.n = 0
    return _make


def login(client, user):
    resp = client.post("/login", data={"email": user.email, "password": "pw"})
    assert resp.status_code == 302, "login failed"
    with client.session_transaction() as sess:
        sess["ai_agents_csrf"] = CSRF


def ba_form(**overrides):
    data = {"csrf_token": CSRF, "problem": "Around 40% of new customers abandon sign-up before verifying their account.",
            "context": "We use Stripe and Auth0.", "constraints": ""}
    data.update(overrides)
    return data


__all__ = ["CSRF", "FakeProvider", "ProviderError", "ba_form", "login", "sample_ba_output", "sample_task_draft", "step_app"]
