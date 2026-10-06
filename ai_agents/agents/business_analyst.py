# Business Analyst Agent: turns a business problem into structured requirements
# and decides whether part of the work should become a STEP student task.

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..registry import BaseAgent, InputField, register_agent
from ..task_bridge import HUMAN_TASK_GUIDANCE, HumanTaskAssessment, NextStep


class BusinessAnalystInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    problem: str = Field(min_length=20, max_length=20000)
    context: str = Field(default="", max_length=20000)
    constraints: str = Field(default="", max_length=5000)


class ProblemDefinition(BaseModel):
    statement: str = Field(description="One-paragraph statement of the real problem, separated from proposed solutions")
    business_context: str = Field(description="Why this matters to the company now")
    objectives: List[str] = Field(description="Measurable or observable outcomes the company wants")
    in_scope: List[str]
    out_of_scope: List[str]
    stakeholders: List[str] = Field(description="Roles affected or involved, e.g. 'Customer support team'")


class FunctionalRequirement(BaseModel):
    id: str = Field(description="FR-1, FR-2, ...")
    description: str = Field(description="What the solution must do, testable and unambiguous")
    priority: Literal["must", "should", "could"]


class NonFunctionalRequirement(BaseModel):
    id: str = Field(description="NFR-1, NFR-2, ...")
    category: str = Field(description="e.g. Performance, Security, Accessibility, Usability, Compliance")
    description: str


class UserStory(BaseModel):
    id: str = Field(description="US-1, US-2, ...")
    role: str = Field(description="The 'As a ...' role")
    goal: str = Field(description="The 'I want ...' goal")
    benefit: str = Field(description="The 'so that ...' benefit")
    acceptance_criteria: List[str] = Field(description="Given/When/Then or equally testable statements")


class Risk(BaseModel):
    description: str
    likelihood: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high"]
    mitigation: str


class BusinessAnalysisOutput(BaseModel):
    summary: str = Field(description="3-5 sentence executive summary of the analysis and the recommended path")
    problem_definition: ProblemDefinition
    functional_requirements: List[FunctionalRequirement]
    non_functional_requirements: List[NonFunctionalRequirement]
    user_stories: List[UserStory]
    risks: List[Risk]
    assumptions: List[str] = Field(description="Assumptions made where the input was silent")
    clarification_questions: List[str] = Field(description="Questions the company should answer to firm up the requirements")
    recommended_next_steps: List[NextStep]
    human_task_assessment: HumanTaskAssessment


SYSTEM_PROMPT = f"""
You are the Business Analyst Agent on STEP. A company describes a business problem; you produce the requirements analysis a capable business analyst would hand to a delivery team.

What good output looks like:
- The problem statement describes the underlying problem, not the company's first idea for a solution. If the company has jumped to a solution, note it and analyse the need behind it.
- Functional requirements are testable and specific to this company. Prioritise with must/should/could; do not mark everything "must".
- Non-functional requirements only where they genuinely apply (for example accessibility for a public website, GDPR where personal data is involved).
- User stories cover the main roles in the problem, each with acceptance criteria a tester could check.
- Risks are specific to this situation, with a practical mitigation.
- Clarification questions target the gaps that would most change the requirements.

{HUMAN_TASK_GUIDANCE}
""".strip()


@register_agent
class BusinessAnalystAgent(BaseAgent):
    slug = "business-analyst"
    name = "Business Analyst"
    tagline = "Turn a business problem into structured requirements and, where it fits, a ready-to-post student task."
    description = (
        "Analyses a business problem and your supporting material, then produces a problem definition, "
        "prioritised requirements, user stories with acceptance criteria, risks and next steps. "
        "If part of the work suits a STEP student, it drafts the task for you to review."
    )
    category = "Analysis & planning"
    icon = "clipboard-data"
    version = "1.0.0"
    capabilities = (
        "Problem definition and scope",
        "Functional and non-functional requirements",
        "User stories with acceptance criteria",
        "Risks, assumptions and open questions",
        "Identifies work suited to a STEP student",
    )
    example_use_cases = (
        "We need to improve our website onboarding process.",
        "Our sales team tracks leads in spreadsheets and keeps losing follow-ups.",
        "We want customers to be able to book appointments online.",
    )

    workspace_prompt = (
        "Describe the business problem you are trying to solve. I will turn it into structured requirements "
        "and, if appropriate, prepare a STEP task for a student."
    )
    input_fields = (
        InputField(
            name="problem",
            label="What are you trying to solve?",
            required=True,
            rows=7,
            placeholder="e.g. Around 40% of new customers abandon sign-up before verifying their account. "
                        "We think the process is too long but we don't know which steps cause the drop-off.",
            help_text="Describe the problem, who it affects and what happens today. At least 20 characters.",
        ),
        InputField(
            name="context",
            label="Background and existing notes",
            rows=5,
            placeholder="Current tools and processes, previous attempts, relevant data, stakeholders…",
            help_text="Optional. Anything the analyst should know that is not in the attached documents.",
        ),
        InputField(
            name="constraints",
            label="Constraints",
            rows=3,
            placeholder="Budget, deadlines, technology you must use, regulations…",
            help_text="Optional.",
        ),
    )
    run_button_label = "Run analysis"

    sample_input = {
        "problem": "Around 40% of new customers abandon our web sign-up before verifying their email address. "
                   "We think the process is too long but we do not know which step causes the drop-off.",
        "context": "B2C subscription app, roughly 2,000 sign-ups a month, email verification required before first use.",
        "constraints": "Small in-house team; must stay GDPR compliant.",
    }

    input_model = BusinessAnalystInput
    output_model = BusinessAnalysisOutput
    system_prompt = SYSTEM_PROMPT

    accepts_files = True
    allowed_extensions = frozenset({".txt", ".md", ".csv", ".pdf", ".docx"})
    max_files = 5

    max_output_tokens = 16000
    result_template = "ai_agents/results/business_analyst.html"

    def build_prompt(self, data: BusinessAnalystInput, documents) -> str:
        return (
            "Produce a business analysis for the following problem.\n\n"
            f"<company_input field=\"problem\">\n{data.problem}\n</company_input>\n\n"
            f"<company_input field=\"background\">\n{data.context or 'None provided.'}\n</company_input>\n\n"
            f"<company_input field=\"constraints\">\n{data.constraints or 'None provided.'}\n</company_input>\n\n"
            f"Supporting documents:\n{self.render_documents(documents)}"
        )

    def run_title(self, data: BusinessAnalystInput) -> str:
        first_line = data.problem.splitlines()[0]
        return first_line if len(first_line) <= 120 else first_line[:117] + "…"

    def task_draft(self, output: BusinessAnalysisOutput):
        assessment = output.human_task_assessment
        return assessment.suggested_task if assessment.recommended else None

    def to_markdown(self, o: BusinessAnalysisOutput, artifacts=None) -> str:
        pd = o.problem_definition
        lines = ["# Business analysis", "", "## Summary", o.summary, "",
                 "## Problem definition", pd.statement, "", f"**Business context:** {pd.business_context}", ""]
        lines += _md_list("### Objectives", pd.objectives)
        lines += _md_list("### In scope", pd.in_scope)
        lines += _md_list("### Out of scope", pd.out_of_scope)
        lines += _md_list("### Stakeholders", pd.stakeholders)

        lines += ["## Functional requirements", "", "| ID | Requirement | Priority |", "|---|---|---|"]
        lines += [f"| {r.id} | {_cell(r.description)} | {r.priority} |" for r in o.functional_requirements]
        lines += ["", "## Non-functional requirements", "", "| ID | Category | Requirement |", "|---|---|---|"]
        lines += [f"| {r.id} | {_cell(r.category)} | {_cell(r.description)} |" for r in o.non_functional_requirements]

        lines += ["", "## User stories", ""]
        for s in o.user_stories:
            lines += [f"**{s.id}.** As a {s.role}, I want {s.goal}, so that {s.benefit}.", ""]
            lines += [f"- {c}" for c in s.acceptance_criteria] + [""]

        lines += ["## Risks", "", "| Risk | Likelihood | Impact | Mitigation |", "|---|---|---|---|"]
        lines += [f"| {_cell(r.description)} | {r.likelihood} | {r.impact} | {_cell(r.mitigation)} |" for r in o.risks]
        lines.append("")
        lines += _md_list("## Assumptions", o.assumptions)
        lines += _md_list("## Questions for clarification", o.clarification_questions)

        owners = {"company": "Company", "ai_agent": "AI agent", "student": "STEP student"}
        lines += ["## Recommended next steps", ""]
        lines += [f"{i}. **{s.step}** ({owners[s.owner]}) - {s.rationale}" for i, s in enumerate(o.recommended_next_steps, 1)]

        a = o.human_task_assessment
        lines += ["", "## Student task recommendation", "",
                  f"**Recommended:** {'Yes' if a.recommended else 'No'}", "", a.rationale, ""]
        if a.recommended and a.suggested_task:
            t = a.suggested_task
            lines += [f"### {t.title}", "", t.description, "", f"**Estimated effort:** {t.estimated_hours} hours", ""]
            lines += _md_list("#### Deliverables", t.deliverables)
            lines += _md_list("#### Acceptance criteria", t.acceptance_criteria)
        return "\n".join(lines).strip() + "\n"


def _md_list(heading: str, items) -> list:
    if not items:
        return []
    return [heading, ""] + [f"- {i}" for i in items] + [""]


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
