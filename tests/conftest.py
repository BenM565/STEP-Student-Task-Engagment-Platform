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
from ai_agents.providers import ProviderError, ResearchResult, Source, StructuredResult, Usage  # noqa: E402

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


def sample_assessment(recommended=False):
    return {"recommended": recommended, "rationale": "Analysis only.",
            "suggested_task": sample_task_draft() if recommended else None}


def sample_next_steps():
    return [{"step": "Review with the team", "owner": "company", "rationale": "Needs a decision"}]


def sample_document_output():
    return {
        "overview": "A supplier contract and its pricing schedule.",
        "documents": [{"filename": "contract.txt", "document_type": "Supplier contract", "summary": "Two-year supply deal.",
                       "key_points": ["Auto-renews"]}],
        "key_information": [
            {"label": "Notice period", "value": "90 days", "source": "contract.txt"},
            {"label": "Monthly fee", "value": "EUR 4,500", "source": "contract.txt"},
        ],
        "risks": [{"description": "Auto-renewal", "severity": "high", "source": "contract.txt", "recommendation": "Diary the notice date"}],
        "inconsistencies": [],
        "answers": [{"question": "When can we terminate?", "status": "answered", "answer": "With 90 days notice.", "sources": ["contract.txt"]}],
        "gaps": ["No data-processing terms"],
        "recommended_next_steps": sample_next_steps(),
        "human_task_assessment": sample_assessment(),
    }


def sample_market_output():
    return {
        "executive_summary": "A growing niche in Cork.",
        "market_overview": [{"point": "Refill shops are expanding", "basis": "sourced", "source_ids": ["S1"]}],
        "size_and_trends": [{"point": "Market grew 12% in 2025", "basis": "sourced", "source_ids": ["S2", "S99"]},
                            {"point": "Likely to keep growing", "basis": "analysis", "source_ids": []}],
        "competitors": [{"name": "Refill Co", "description": "Cork refill shop", "positioning": "Premium",
                         "strengths": ["Location"], "weaknesses": ["Price"], "source_ids": ["S1"]}],
        "customer_segments": [{"name": "Eco households", "description": "Families", "needs": ["Convenience"], "source_ids": []}],
        "opportunities": [{"point": "Office deliveries", "basis": "analysis", "source_ids": []}],
        "risks": [{"point": "Rents rising", "basis": "sourced", "source_ids": ["S2"]}],
        "evidence_gaps": ["No local market size figure"],
        "recommended_next_steps": sample_next_steps(),
        "human_task_assessment": sample_assessment(recommended=True),
    }


def sample_data_output():
    return {
        "dataset_summary": "Orders by region.",
        "plain_english_summary": "Cork sells the most.",
        "answer_to_question": None,
        "key_findings": [
            {"title": "Cork leads", "detail": "Cork has the most revenue.", "evidence": "Cork revenue sum 300", "importance": "high"},
            {"title": "Growth", "detail": "Revenue grew.", "evidence": "Revenue grew 37.5%", "importance": "medium"},
        ],
        "trends": [], "anomalies": [], "data_quality_issues": [],
        "suggested_visualisations": [
            {"title": "Revenue by region", "chart_type": "bar", "x_column": "region", "y_column": "revenue", "aggregation": "sum", "rationale": "Compare regions"},
            {"title": "Bad chart", "chart_type": "line", "x_column": "nonexistent", "y_column": None, "aggregation": "count", "rationale": "x"},
        ],
        "recommended_next_steps": sample_next_steps(),
        "human_task_assessment": sample_assessment(),
    }


DEFAULT_OUTPUTS = {
    "BusinessAnalysisOutput": sample_ba_output,
    "DocumentAnalysisOutput": sample_document_output,
    "MarketResearchOutput": sample_market_output,
    "DataAnalysisOutput": sample_data_output,
}


class FakeProvider:
    """Implements the AIProvider interface; returns canned output (per output model) or raises."""

    name = "fake"

    def __init__(self):
        self.calls = []
        self.output = None  # set to override the default output for every model
        self.error = None
        self.research_error = None
        self.research_sources = [Source(url="https://example.ie/refill-report", title="Refill report 2025", page_age="2025-06-01"),
                                 Source(url="https://stats.example.ie/retail", title="Retail statistics")]

    def generate(self, **kwargs):  # pragma: no cover - not used by current agents
        raise NotImplementedError

    def research(self, *, system, prompt, max_searches=8, max_tokens=16000, effort=None):
        self.calls.append({"kind": "research", "system": system, "prompt": prompt, "max_searches": max_searches})
        if self.research_error:
            raise self.research_error
        return ResearchResult(text="Notes: refill shops growing [S1].", sources=list(self.research_sources),
                              model="claude-opus-5-5", provider=self.name,
                              usage=Usage(input_tokens=2000, output_tokens=800, web_search_requests=3))

    def structured_output(self, *, system, prompt, output_model, max_tokens=16000, effort=None):
        self.calls.append({"kind": "structured", "system": system, "prompt": prompt, "output_model": output_model,
                           "max_tokens": max_tokens, "effort": effort})
        if self.error:
            raise self.error
        data = self.output if self.output is not None else DEFAULT_OUTPUTS[output_model.__name__]()
        return StructuredResult(output=output_model.model_validate(data), model="claude-opus-5-5",
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
        AI_EXECUTION_MODE="inline",
        AI_WEB_SEARCH_ENABLED=True,
        AI_MAX_RUNS_PER_DAY=50,
        SUBMISSION_UPLOAD_DIR=str(tmp_path / "submissions"),
        MAIL_EVENT_EMAILS=True,
        ANTHROPIC_API_KEY=None,
    )
    flask_app.fake_provider = provider
    flask_app.extensions["outbox"] = []
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


def login(client, user, password="pw"):
    # Every POST needs the session's CSRF token; seed it before logging in
    with client.session_transaction() as sess:
        sess["_csrf_token"] = CSRF
    resp = client.post("/login", data={"email": user.email, "password": password, "csrf_token": CSRF})
    assert resp.status_code == 302, "login failed"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = CSRF


def ba_form(**overrides):
    data = {"csrf_token": CSRF, "problem": "Around 40% of new customers abandon sign-up before verifying their account.",
            "context": "We use Stripe and Auth0.", "constraints": ""}
    data.update(overrides)
    return data


__all__ = ["CSRF", "FakeProvider", "ProviderError", "ba_form", "login", "sample_ba_output", "sample_task_draft", "step_app"]
