# Process Automation Advisor: maps a manual business process, finds what can be
# automated (and what should stay human), and proposes a phased roadmap plus a
# STEP task for building the first automation.

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..registry import BaseAgent, InputField, register_agent
from ..task_bridge import HUMAN_TASK_GUIDANCE, HumanTaskAssessment, NextStep


class ProcessAutomationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    process: str = Field(min_length=30, max_length=15000)
    volume: str = Field(default="", max_length=1000)
    tools: str = Field(default="", max_length=2000)
    constraints: str = Field(default="", max_length=3000)


class ProcessStep(BaseModel):
    step: str
    actor: str = Field(description="Role that does it today, e.g. 'Accounts assistant'")
    time_per_occurrence: str = Field(description="Time per occurrence as stated by the company, or 'not stated'")
    pain_points: List[str]


class Opportunity(BaseModel):
    title: str
    steps_affected: List[str]
    approach: str = Field(description="What would change and how it would work")
    automation_level: Literal["full", "partial", "assistive"] = Field(
        description="full = no human needed; partial = human checks exceptions; assistive = tool helps a person"
    )
    tools: List[str] = Field(description="Tool categories or common products, preferring what the company already uses")
    effort: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high"]
    time_saving_estimate: str = Field(
        description="Estimated saving with its arithmetic and assumptions, using only figures the company gave; "
                    "'cannot estimate: <missing figure>' otherwise"
    )
    risks: List[str]


class KeepHuman(BaseModel):
    activity: str
    reason: str = Field(description="Why this should stay with a person (judgement, relationships, accountability, regulation)")


class RoadmapPhase(BaseModel):
    phase: str
    description: str
    opportunities: List[str] = Field(description="Titles of opportunities delivered in this phase")
    indicative_duration: str


class ProcessAutomationOutput(BaseModel):
    summary: str
    current_process: List[ProcessStep]
    opportunities: List[Opportunity] = Field(description="Ordered by impact relative to effort, best first")
    quick_wins: List[str] = Field(description="Changes the company could make this month with existing tools")
    keep_human: List[KeepHuman]
    roadmap: List[RoadmapPhase]
    assumptions: List[str]
    questions: List[str] = Field(description="What the company should answer to firm up the plan")
    recommended_next_steps: List[NextStep]
    human_task_assessment: HumanTaskAssessment


SYSTEM_PROMPT = f"""
You are the Process Automation Advisor on STEP. A company describes a manual or repetitive process; you show them what to automate, what to keep human, and how to get there.

How to work:
- Map the current process step by step from the description. Do not invent steps; where the description is vague, note it in questions.
- Prefer improving and connecting the tools the company already uses before recommending new products. Name tool categories (e.g. "workflow automation platform", "OCR for invoices") and well-known options as examples, not endorsements.
- Rank opportunities by impact relative to effort. Be explicit about whether each is full automation, partial (a person handles exceptions) or assistive.
- Time-saving estimates must show their arithmetic from figures the company gave (for example volume per week times minutes per item). If a figure is missing, write "cannot estimate" and ask for it. Never present an invented number as fact.
- Name the activities that should stay human: judgement calls, customer relationships, approvals with accountability, and anything regulated.
- Flag data protection (GDPR), security and change-management risks where relevant.

{HUMAN_TASK_GUIDANCE}
Typical student work here: building a prototype automation or integration, mapping the process with the team, cleaning the data an automation needs, or documenting the new workflow.
""".strip()


@register_agent
class ProcessAutomationAgent(BaseAgent):
    slug = "process-automation"
    name = "Process Automation Advisor"
    tagline = "Find which parts of a manual process to automate, which to keep human, and how to start."
    description = (
        "Maps a repetitive business process, ranks automation opportunities by impact and effort, estimates time "
        "saved from your own figures, identifies what should stay with people, and lays out a phased roadmap."
    )
    category = "Operations"
    icon = "gear-wide-connected"
    version = "1.0.0"
    capabilities = (
        "Step-by-step map of the current process",
        "Automation opportunities ranked by impact and effort",
        "Time-saving estimates with the arithmetic shown",
        "What should stay human, and why",
        "Phased roadmap and a student task for the first build",
    )
    example_use_cases = (
        "We copy supplier invoices from email into our accounting system by hand.",
        "New client onboarding involves five spreadsheets and lots of chasing.",
        "Our weekly sales report takes a day to put together.",
    )

    workspace_prompt = (
        "Describe a process your team does repeatedly, step by step if you can. I will show you what to automate, "
        "what to keep human, and how to get started."
    )
    input_fields = (
        InputField(name="process", label="Describe the process", required=True, rows=7,
                   placeholder="Who does what, in which tools, and where it goes wrong. e.g. Invoices arrive by email; "
                               "an assistant opens each PDF, types the details into Xero, then files the PDF...",
                   help_text="At least 30 characters. The more concrete, the better the recommendations."),
        InputField(name="volume", label="How often and how long?", widget="text",
                   placeholder="e.g. About 200 invoices a month, roughly 6 minutes each",
                   help_text="Optional, but needed for time-saving estimates."),
        InputField(name="tools", label="Tools you use today", rows=2,
                   placeholder="e.g. Outlook, Xero, Google Drive, Excel", help_text="Optional."),
        InputField(name="constraints", label="Constraints", rows=2,
                   placeholder="Budget, IT policies, data that can't leave the EU…", help_text="Optional."),
    )
    run_button_label = "Analyse process"

    input_model = ProcessAutomationInput
    output_model = ProcessAutomationOutput
    system_prompt = SYSTEM_PROMPT

    accepts_files = True
    allowed_extensions = frozenset({".pdf", ".docx", ".txt", ".md", ".csv"})
    max_files = 3
    files_help = "Optional. Process documents, SOPs or example outputs."

    max_output_tokens = 16000
    result_template = "ai_agents/results/process_automation.html"

    sample_input = {
        "process": "Supplier invoices arrive by email. An accounts assistant opens each PDF, types supplier, date, "
                   "amount and VAT into Xero, checks it against the purchase order in a shared spreadsheet, then "
                   "saves the PDF to Google Drive. Mismatches are emailed to the buyer.",
        "volume": "About 200 invoices a month, 6 minutes each",
        "tools": "Outlook, Xero, Google Drive, Google Sheets",
    }

    def build_prompt(self, data: ProcessAutomationInput, documents) -> str:
        return (
            "Analyse this process for automation.\n\n"
            f"<company_input field=\"process\">\n{data.process}\n</company_input>\n\n"
            f"<company_input field=\"volume\">\n{data.volume or 'Not stated.'}\n</company_input>\n\n"
            f"<company_input field=\"tools\">\n{data.tools or 'Not stated.'}\n</company_input>\n\n"
            f"<company_input field=\"constraints\">\n{data.constraints or 'None stated.'}\n</company_input>\n\n"
            f"Supporting documents:\n{self.render_documents(documents)}"
        )

    def run_title(self, data: ProcessAutomationInput) -> str:
        first = data.process.splitlines()[0]
        return first if len(first) <= 120 else first[:117] + "…"

    def task_draft(self, output: ProcessAutomationOutput):
        a = output.human_task_assessment
        return a.suggested_task if a.recommended else None

    def to_markdown(self, o: ProcessAutomationOutput, artifacts=None) -> str:
        lines = ["# Process automation review", "", o.summary, "", "## Current process", ""]
        lines += [f"{i}. **{s.step}** ({s.actor}, {s.time_per_occurrence})" + (f" - pain points: {'; '.join(s.pain_points)}" if s.pain_points else "")
                  for i, s in enumerate(o.current_process, 1)]
        lines += ["", "## Opportunities", ""]
        for op in o.opportunities:
            lines += [f"### {op.title} ({op.automation_level}; effort {op.effort}, impact {op.impact})", "", op.approach, "",
                      f"**Tools:** {', '.join(op.tools) or '-'}", f"**Time saving:** {op.time_saving_estimate}", ""]
            lines += [f"- Risk: {r}" for r in op.risks] + [""]
        if o.quick_wins:
            lines += ["## Quick wins", ""] + [f"- {q}" for q in o.quick_wins] + [""]
        if o.keep_human:
            lines += ["## Keep human", ""] + [f"- **{k.activity}**: {k.reason}" for k in o.keep_human] + [""]
        lines += ["## Roadmap", ""] + [f"{i}. **{p.phase}** ({p.indicative_duration}): {p.description}" for i, p in enumerate(o.roadmap, 1)]
        if o.assumptions:
            lines += ["", "## Assumptions", ""] + [f"- {a}" for a in o.assumptions]
        if o.questions:
            lines += ["", "## Questions", ""] + [f"- {q}" for q in o.questions]
        lines += ["", "## Recommended next steps", ""] + [f"{i}. **{s.step}** - {s.rationale}" for i, s in enumerate(o.recommended_next_steps, 1)]
        return "\n".join(lines).strip() + "\n"
