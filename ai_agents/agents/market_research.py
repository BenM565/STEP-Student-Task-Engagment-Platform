# Market Research Agent: two steps.
#   1. research: the model searches the web (server-side web search) and writes notes
#   2. structure: a schema-constrained call turns the notes into the report
# Claims can only cite sources that step 1 actually retrieved; anything else is
# shown as the AI's own analysis, not as a sourced fact.

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..providers import ProviderError
from ..registry import SHARED_GUARDRAILS, AgentResult, BaseAgent, InputField, register_agent
from ..task_bridge import HUMAN_TASK_GUIDANCE, HumanTaskAssessment, NextStep


class MarketResearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    idea: str = Field(min_length=20, max_length=8000)
    target_market: str = Field(default="", max_length=2000)
    geography: str = Field(min_length=2, max_length=200)
    known_competitors: str = Field(default="", max_length=2000)


class Point(BaseModel):
    point: str
    basis: Literal["sourced", "analysis"] = Field(
        description="sourced = stated by the cited sources; analysis = your own inference from them"
    )
    source_ids: List[str] = Field(description="IDs such as 'S3' from the source list; empty for analysis")


class Competitor(BaseModel):
    name: str
    description: str
    positioning: str = Field(description="Who they target and how they compete (price, features, channel)")
    strengths: List[str]
    weaknesses: List[str]
    source_ids: List[str]


class CustomerSegment(BaseModel):
    name: str
    description: str
    needs: List[str]
    source_ids: List[str]


class MarketResearchOutput(BaseModel):
    executive_summary: str
    market_overview: List[Point]
    size_and_trends: List[Point] = Field(description="Market size, growth and trends; figures must cite sources")
    competitors: List[Competitor]
    customer_segments: List[CustomerSegment]
    opportunities: List[Point]
    risks: List[Point]
    evidence_gaps: List[str] = Field(description="Important questions the research could not answer with sources")
    recommended_next_steps: List[NextStep]
    human_task_assessment: HumanTaskAssessment


RESEARCH_PROMPT = """
You are the research step of STEP's Market Research Agent. Use web search to gather current, verifiable evidence about the company's idea in the given geography: market size and growth, trends, direct and indirect competitors (with their positioning and pricing where published), customer segments and their needs, regulation, and risks.

Prefer primary and reputable sources (official statistics, regulators, company websites, established research and news outlets). Note the date of figures. Search for the specific geography rather than assuming global figures apply.

Write thorough research notes. For each finding, say which page supports it. Clearly separate what sources state from your own interpretation, and list what you looked for but could not find.
""".strip()

STRUCTURE_PROMPT = f"""
You are STEP's Market Research Agent. Turn the research notes into a market research report for the company.

Rules for evidence:
- Cite sources by their IDs (S1, S2, ...) from the source list. Use only those IDs.
- Every market figure (size, growth rate, share, price) must have basis "sourced" and at least one source ID. If the notes give no source for a figure, leave the figure out and add the question to evidence_gaps.
- Use basis "analysis" for your own reasoning, and leave its source_ids empty.
- Do not add competitors or facts that are not in the research notes.

{HUMAN_TASK_GUIDANCE}
Typical student work after desk research: customer interviews or surveys, competitor mystery-shopping, pricing tests, landing-page experiments.
""".strip()


@register_agent
class MarketResearchAgent(BaseAgent):
    slug = "market-research"
    name = "Market Researcher"
    tagline = "Research a market, its competitors and opportunities, using live web sources."
    description = (
        "Searches the web for current evidence on your market and turns it into an overview, competitor analysis, "
        "customer segments, opportunities, risks and next steps. Every figure links to the page it came from."
    )
    category = "Research"
    icon = "globe2"
    version = "1.0.0"
    capabilities = (
        "Live web research with linked sources",
        "Market overview, size and trends",
        "Competitor analysis",
        "Customer segments and needs",
        "Opportunities, risks and evidence gaps",
    )
    example_use_cases = (
        "A subscription meal-kit service for students in Ireland.",
        "B2B software for scheduling home-care visits in the UK.",
        "Expanding our refill-station shops from Cork to Dublin.",
    )

    workspace_prompt = (
        "Describe the business or product idea and where you want to sell it. I will research the market on the web "
        "and give you a sourced overview, competitors, customer segments, opportunities and risks."
    )
    input_fields = (
        InputField(name="idea", label="Business or product idea", required=True, rows=5,
                   placeholder="What you sell or plan to sell, to whom, and how it makes money.",
                   help_text="At least 20 characters."),
        InputField(name="geography", label="Geography", widget="text", required=True,
                   placeholder="e.g. Ireland, Dublin, UK and Ireland", help_text="Where you want to sell."),
        InputField(name="target_market", label="Target customers", rows=2,
                   placeholder="e.g. Small dental practices with 1-5 dentists", help_text="Optional."),
        InputField(name="known_competitors", label="Competitors you already know", rows=2,
                   placeholder="Names or websites, comma separated", help_text="Optional."),
    )
    run_button_label = "Research market"

    input_model = MarketResearchInput
    output_model = MarketResearchOutput
    system_prompt = STRUCTURE_PROMPT

    accepts_files = True
    allowed_extensions = frozenset({".pdf", ".docx", ".txt", ".md"})
    max_files = 3
    files_label = "Business plan or notes"
    files_help = "Optional. Context about your business; it is not shared outside this analysis."

    requires_web_search = True
    max_output_tokens = 16000
    result_template = "ai_agents/results/market_research.html"

    sample_input = {
        "idea": "A refill-station shop selling household cleaning products and dry goods by weight, to cut plastic packaging.",
        "geography": "Cork, Ireland",
        "target_market": "Environmentally conscious households",
    }

    def build_prompt(self, data: MarketResearchInput, documents) -> str:
        return (
            f"<company_input field=\"idea\">\n{data.idea}\n</company_input>\n\n"
            f"<company_input field=\"geography\">\n{data.geography}\n</company_input>\n\n"
            f"<company_input field=\"target_customers\">\n{data.target_market or 'Not specified.'}\n</company_input>\n\n"
            f"<company_input field=\"known_competitors\">\n{data.known_competitors or 'None given.'}\n</company_input>\n\n"
            f"Company documents:\n{self.render_documents(documents)}"
        )

    def execute(self, ctx) -> AgentResult:
        brief = self.build_prompt(ctx.data, ctx.documents)
        research = ctx.provider.research(
            system=f"{RESEARCH_PROMPT}\n\n{SHARED_GUARDRAILS}",
            prompt=f"Research this market.\n\n{brief}",
            max_searches=int(ctx.config.get("AI_WEB_SEARCH_MAX_USES", 8)),
            effort=self.effort,
        )
        ctx.record(research)
        if not any(s.url.lower().startswith(("https://", "http://")) for s in research.sources):
            raise ProviderError("no_sources", "The web research did not return any sources, so no report was produced. "
                                              "Please try again, or describe the market more specifically.")

        # Only http(s) links are kept: they are rendered as clickable hrefs
        web = [s for s in research.sources if s.url.lower().startswith(("https://", "http://"))]
        sources = [{"id": f"S{i}", "url": s.url, "title": s.title or s.url, "page_age": s.page_age}
                   for i, s in enumerate(web, 1)]
        source_list = "\n".join(f"[{s['id']}] {s['title']} - {s['url']}" + (f" ({s['page_age']})" if s["page_age"] else "")
                                for s in sources)
        structured = ctx.provider.structured_output(
            system=self.full_system_prompt(),
            prompt=(f"{brief}\n\n<research_notes>\n{research.text}\n</research_notes>\n\n"
                    f"<sources>\n{source_list}\n</sources>"),
            output_model=self.output_model,
            max_tokens=self.max_output_tokens,
            effort=self.effort,
        )
        ctx.record(structured)
        return AgentResult(output=structured.output, artifacts={"sources": sources, "research_notes": research.text})

    def run_title(self, data: MarketResearchInput) -> str:
        first = data.idea.splitlines()[0]
        title = f"{first[:90]}{'…' if len(first) > 90 else ''} ({data.geography})"
        return title[:120]

    def task_draft(self, output: MarketResearchOutput):
        a = output.human_task_assessment
        return a.suggested_task if a.recommended else None

    def to_markdown(self, o: MarketResearchOutput, artifacts=None) -> str:
        lines = ["# Market research", "", o.executive_summary, ""]

        def points(heading, items):
            if items:
                lines.extend([f"## {heading}", ""] + [f"- {p.point}{_refs(p.source_ids) if p.basis == 'sourced' else ' (analysis)'}" for p in items] + [""])

        points("Market overview", o.market_overview)
        points("Size and trends", o.size_and_trends)
        if o.competitors:
            lines += ["## Competitors", ""]
            for c in o.competitors:
                lines += [f"### {c.name}{_refs(c.source_ids)}", "", c.description, "", f"**Positioning:** {c.positioning}", ""]
                lines += [f"- Strength: {s}" for s in c.strengths] + [f"- Weakness: {w}" for w in c.weaknesses] + [""]
        if o.customer_segments:
            lines += ["## Customer segments", ""]
            for seg in o.customer_segments:
                lines += [f"- **{seg.name}**{_refs(seg.source_ids)}: {seg.description} Needs: {'; '.join(seg.needs)}"]
            lines.append("")
        points("Opportunities", o.opportunities)
        points("Risks", o.risks)
        if o.evidence_gaps:
            lines += ["## Evidence gaps", ""] + [f"- {g}" for g in o.evidence_gaps] + [""]
        lines += ["## Recommended next steps", ""] + [f"{i}. **{s.step}** - {s.rationale}" for i, s in enumerate(o.recommended_next_steps, 1)]
        if artifacts and artifacts.get("sources"):
            lines += ["", "## Sources", ""] + [f"- [{s['id']}] {s['title']}: {s['url']}" for s in artifacts["sources"]]
        return "\n".join(lines).strip() + "\n"


def _refs(ids) -> str:
    return f" [{', '.join(ids)}]" if ids else ""

