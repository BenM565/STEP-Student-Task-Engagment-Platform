# Document Analysis, Market Research and Data Analysis agents end to end.

import io
import json

from conftest import CSRF, ProviderError, login, step_app

from ai_agents.models import AgentRun, AIAgent
from ai_agents.providers import Source, Usage

CONTRACT = (b"Supplier agreement. Either party may terminate with 90 days notice. "
            b"The monthly fee is EUR 4,500. The agreement renews automatically.")


def _post(client, slug, data, files=None):
    payload = {"csrf_token": CSRF, **data}
    if files is not None:
        payload["files"] = files
    return client.post(f"/company/ai-agents/{slug}", data=payload, content_type="multipart/form-data")


# --- Document Analysis -------------------------------------------------------

def test_document_analysis_requires_a_document(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "document-analysis", {"questions": "When can we terminate?"})
    assert resp.status_code == 400 and "attach at least 1 file" in resp.get_data(as_text=True)
    assert AgentRun.query.count() == 0


def test_document_analysis_end_to_end_with_fact_checks(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "document-analysis", {"questions": "When can we terminate?\nWhat is the fee?", "focus": "costs"},
                 [(io.BytesIO(CONTRACT), "contract.txt")])
    run = AgentRun.query.one()
    assert resp.status_code == 302 and run.status == "succeeded"
    assert run.title == "When can we terminate?"

    prompt = app.fake_provider.calls[0]["prompt"]
    assert "1. When can we terminate?\n2. What is the fee?" in prompt
    assert "90 days notice" in prompt
    # "90 days" appears in the text; "EUR 4,500" does too; both verified
    assert run.artifacts["fact_checks"] == [True, True]

    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    for text in ("Answers to your questions", "Found in text", "Auto-renewal", "No data-processing terms"):
        assert text in html, text


def test_document_fact_not_in_text_is_flagged(app, client, make_user):
    from conftest import sample_document_output

    output = sample_document_output()
    output["key_information"][1]["value"] = "EUR 5,000"  # not what the document says
    app.fake_provider.output = output
    login(client, make_user())
    _post(client, "document-analysis", {}, [(io.BytesIO(CONTRACT), "contract.txt")])
    run = AgentRun.query.one()
    assert run.artifacts["fact_checks"] == [True, False]
    assert "Check source" in client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)


# --- Market Research ---------------------------------------------------------

MR = {"idea": "A refill-station shop for cleaning products in Cork city centre.", "geography": "Cork, Ireland"}


def test_market_research_researches_then_structures(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "market-research", MR)
    run = AgentRun.query.one()
    assert resp.status_code == 302 and run.status == "succeeded", run.error_message

    research, structured = app.fake_provider.calls
    assert research["kind"] == "research" and research["max_searches"] == 8
    assert "Cork, Ireland" in research["prompt"] and "<company_input" in research["system"]
    assert structured["kind"] == "structured"
    assert "[S1] Refill report 2025 - https://example.ie/refill-report (2025-06-01)" in structured["prompt"]
    assert "<research_notes>" in structured["prompt"]

    # Usage from both calls, including web searches, is recorded and priced
    assert (run.input_tokens, run.output_tokens, run.web_search_requests) == (3000, 1300, 3)
    expected = (3000 * 4 + 1300 * 20) / 1_000_000 + 3 * 10 / 1000
    assert abs(float(run.cost_usd) - expected) < 1e-6

    assert [s["id"] for s in run.artifacts["sources"]] == ["S1", "S2"]
    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    assert 'href="https://example.ie/refill-report"' in html and 'rel="noopener noreferrer nofollow"' in html
    assert "AI analysis" in html and "3 web searches" in html
    # S99 does not exist and is not rendered as a citation
    assert "S99" not in html
    md = client.get(f"/company/ai-agents/runs/{run.id}/export?format=md").get_data(as_text=True)
    assert "## Sources" in md and "https://stats.example.ie/retail" in md


def test_market_research_without_sources_fails(app, client, make_user):
    app.fake_provider.research_sources = [Source(url="javascript:alert(1)", title="bad")]
    login(client, make_user())
    _post(client, "market-research", MR)
    run = AgentRun.query.one()
    assert run.status == "failed" and run.error_code == "no_sources"
    assert run.web_search_requests == 3  # research usage still recorded
    assert len(app.fake_provider.calls) == 1  # no report generated


def test_market_research_hidden_when_web_search_disabled(app, client, make_user):
    app.config["AI_WEB_SEARCH_ENABLED"] = False
    login(client, make_user())
    assert "Market Researcher" not in client.get("/company/ai-agents").get_data(as_text=True)
    assert client.get("/company/ai-agents/market-research").status_code == 404


def test_market_research_failure_in_second_step_keeps_first_step_cost(app, client, make_user):
    app.fake_provider.error = ProviderError("rate_limited", "Busy.", usage=Usage(input_tokens=5, output_tokens=0))
    login(client, make_user())
    _post(client, "market-research", MR)
    run = AgentRun.query.one()
    assert run.status == "failed"
    assert (run.input_tokens, run.web_search_requests) == (2005, 3)


def test_market_research_requires_geography(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "market-research", {"idea": MR["idea"], "geography": ""})
    assert resp.status_code == 400 and AgentRun.query.count() == 0


# --- Data Analysis -----------------------------------------------------------

def _sales_csv() -> bytes:
    rows = ["order_date,region,revenue"]
    data = [("05/01/2025", "Cork", 100), ("06/01/2025", "Dublin", 50), ("03/02/2025", "Cork", 200),
            ("04/02/2025", "Galway", 25), ("10/03/2025", "Dublin", 75)]
    rows += [f"{d},{r},{v}" for d, r, v in data]
    return "\n".join(rows).encode()


def test_data_analysis_profiles_and_draws_charts(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "data-analysis", {"question": "Which region sells most?"}, [(io.BytesIO(_sales_csv()), "sales.csv")])
    run = AgentRun.query.one()
    assert resp.status_code == 302 and run.status == "succeeded", run.error_message

    prompt = app.fake_provider.calls[0]["prompt"]
    profile = json.loads(prompt.split("<data_profile>\n")[1].split("\n</data_profile>")[0])
    revenue = next(c for c in profile["column_profiles"] if c["name"] == "revenue")
    assert revenue["type"] == "numeric" and revenue["stats"]["sum"] == 450 and revenue["stats"]["median"] == 75
    assert next(c for c in profile["column_profiles"] if c["name"] == "order_date")["type"] == "date"

    good, bad = run.artifacts["charts"]
    assert good["ok"] and good["labels"] == ["Cork", "Dublin", "Galway"] and good["values"] == [300, 125, 25]
    assert not bad["ok"] and "not in the dataset" in bad["error"]
    # "Cork revenue sum 300" is in the profile; "37.5%" is not
    assert run.artifacts["figure_checks"]["key_findings"] == [True, False]

    html = client.get(f"/company/ai-agents/runs/{run.id}").get_data(as_text=True)
    for text in ("Figures match data", "Check figures", "Not drawn:", "chart.umd.min.js", "Data profile computed by STEP"):
        assert text in html, text


def test_data_analysis_reads_xlsx(app, client, make_user):
    import openpyxl
    from datetime import datetime

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["order_date", "region", "revenue"])
    ws.append([datetime(2025, 1, 5), "Cork", 100])
    ws.append([datetime(2025, 2, 3), "Dublin", 300.5])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    login(client, make_user())
    _post(client, "data-analysis", {}, [(buf, "sales.xlsx")])
    run = AgentRun.query.one()
    assert run.status == "succeeded", run.error_message
    revenue = next(c for c in run.artifacts["profile"]["column_profiles"] if c["name"] == "revenue")
    assert revenue["stats"]["sum"] == 400.5


def test_data_analysis_accepts_one_dataset_only(app, client, make_user):
    login(client, make_user())
    resp = _post(client, "data-analysis", {}, [(io.BytesIO(_sales_csv()), "a.csv"), (io.BytesIO(_sales_csv()), "b.csv")])
    assert resp.status_code == 400 and "Attach at most 1 file." in resp.get_data(as_text=True)
    resp = _post(client, "data-analysis", {}, [(io.BytesIO(b"hello"), "notes.txt")])
    assert resp.status_code == 400 and "not a supported file type" in resp.get_data(as_text=True)


def test_data_analysis_bad_dataset_fails_cleanly(app, client, make_user):
    login(client, make_user())
    _post(client, "data-analysis", {}, [(io.BytesIO(b"only_a_header\n"), "empty.csv")])
    run = AgentRun.query.one()
    assert run.status == "failed" and run.error_code == "invalid_dataset"
    assert "no data rows" in run.error_message
    assert app.fake_provider.calls == []


def test_all_agents_listed_in_catalogue(app, client, make_user):
    login(client, make_user())
    html = client.get("/company/ai-agents").get_data(as_text=True)
    for name in ("Business Analyst", "Document Analyst", "Market Researcher", "Data Analyst", "Requirements-to-Task",
                 "Process Automation Advisor"):
        assert name in html
    assert AIAgent.query.count() == 6


def test_new_agent_task_handoff(app, client, make_user):
    # Market research sample output recommends a student task
    login(client, make_user())
    _post(client, "market-research", MR)
    run = AgentRun.query.one()
    form = client.get(f"/tasks/new?from_run={run.id}").get_data(as_text=True)
    assert 'value="User research and redesign proposal for website onboarding"' in form
    assert step_app.Task.query.count() == 0
