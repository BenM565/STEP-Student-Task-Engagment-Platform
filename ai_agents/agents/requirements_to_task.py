# Requirements-to-Task Agent: converts a vague need straight into a scoped
# STEP student task, and is explicit about what AI can do versus a student.

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from ..registry import BaseAgent, InputField, register_agent
from ..task_bridge import StepTaskDraft


class RequirementsToTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    need: str = Field(min_length=15, max_length=10000)
    context: str = Field(default="", max_length=15000)
    constraints: str = Field(default="", max_length=5000)


class RequirementsToTaskOutput(BaseModel):
    interpretation: str = Field(description="2-3 sentences restating what the company actually needs, in concrete terms")
    suitable_for_student: bool = Field(description="Whether this work, as scoped below, is appropriate for a STEP student")
    suitability_rationale: str
    task: StepTaskDraft
    ai_can_handle: List[str] = Field(description="Parts of the work an AI agent could do now, before or alongside the student")
    scoping_notes: List[str] = Field(description="What was deliberately left out of the task and why, e.g. phased work")


SYSTEM_PROMPT = """
You are the Requirements-to-Task Agent on STEP. A company gives you a need, often vague ("we need to improve our onboarding"). You turn it into one well-scoped task a university student can complete remotely, which the company will review and post on STEP.

How to scope:
- Interpret the need concretely. If it is broad, scope a valuable first phase rather than the whole programme, and explain the rest in scoping_notes.
- Deliverables must be tangible artefacts (a report, a prototype, a cleaned dataset, a tested feature). Acceptance criteria must be checkable by the company without specialist knowledge.
- Milestones should let the company see progress early, normally with a check-in within the first quarter of the effort.
- Estimate hours honestly for a capable student, including research and write-up time.
- Set suitable_for_student to false when the work requires unsupervised access to production systems or sensitive personal data, regulated professional sign-off, or is mainly a company decision. Still return the best student-appropriate task you can, and explain the limitation.
- List in ai_can_handle the analysis or drafting an AI agent could do immediately, so the student's time goes on work that needs a person.
""".strip()


@register_agent
class RequirementsToTaskAgent(BaseAgent):
    slug = "requirements-to-task"
    name = "Requirements-to-Task"
    tagline = "Turn a vague need into a scoped, ready-to-post STEP task for a student."
    description = (
        "Takes a loosely defined need and produces a STEP task with objectives, required skills, deliverables, "
        "milestones, acceptance criteria and an effort estimate, plus the questions you should answer first."
    )
    category = "STEP tasks"
    icon = "list-check"
    version = "1.0.0"
    capabilities = (
        "Interprets vague requests into concrete work",
        "Scopes a realistic first phase",
        "Deliverables, milestones and acceptance criteria",
        "Effort estimate and suited student disciplines",
        "Separates AI-ready work from student work",
    )
    example_use_cases = (
        "We need to improve our website onboarding process.",
        "Can someone look into why our social media engagement dropped?",
        "We want a dashboard for our weekly sales numbers.",
    )

    workspace_prompt = (
        "Describe the work you need done, even if it is still vague. I will scope it into a STEP task "
        "a student can deliver, which you can edit before publishing."
    )
    input_fields = (
        InputField(
            name="need",
            label="What do you need done?",
            required=True,
            rows=5,
            placeholder="e.g. We need to improve our website onboarding process.",
            help_text="A sentence or two is enough. At least 15 characters.",
        ),
        InputField(
            name="context",
            label="Useful background",
            rows=4,
            placeholder="Your product, current process, what you have tried, tools you use…",
            help_text="Optional.",
        ),
        InputField(
            name="constraints",
            label="Constraints",
            rows=2,
            placeholder="Budget in hours, deadline, required tools…",
            help_text="Optional.",
        ),
    )
    run_button_label = "Draft STEP task"

    sample_input = {"need": "We need to improve our website onboarding process.",
                    "context": "Small online retailer; most new customers sign up on mobile."}

    input_model = RequirementsToTaskInput
    output_model = RequirementsToTaskOutput
    system_prompt = SYSTEM_PROMPT

    accepts_files = True
    allowed_extensions = frozenset({".txt", ".md", ".pdf", ".docx"})
    max_files = 3

    max_output_tokens = 8000
    result_template = "ai_agents/results/requirements_to_task.html"

    def build_prompt(self, data: RequirementsToTaskInput, documents) -> str:
        return (
            "Scope the following need into a STEP student task.\n\n"
            f"<company_input field=\"need\">\n{data.need}\n</company_input>\n\n"
            f"<company_input field=\"background\">\n{data.context or 'None provided.'}\n</company_input>\n\n"
            f"<company_input field=\"constraints\">\n{data.constraints or 'None provided.'}\n</company_input>\n\n"
            f"Supporting documents:\n{self.render_documents(documents)}"
        )

    def run_title(self, data: RequirementsToTaskInput) -> str:
        first_line = data.need.splitlines()[0]
        return first_line if len(first_line) <= 120 else first_line[:117] + "…"

    def task_draft(self, output: RequirementsToTaskOutput):
        return output.task

    def to_markdown(self, o: RequirementsToTaskOutput, artifacts=None) -> str:
        t = o.task
        lines = ["# STEP task draft", "", o.interpretation, "",
                 f"**Suitable for a student:** {'Yes' if o.suitable_for_student else 'No'} - {o.suitability_rationale}", "",
                 f"## {t.title}", "", t.description, "", f"**Estimated effort:** {t.estimated_hours} hours", ""]
        for heading, items in (
            ("Objectives", t.objectives),
            ("Required skills", t.required_skills),
            ("Deliverables", t.deliverables),
            ("Milestones", [f"{m.name} - {m.description}" for m in t.milestones]),
            ("Acceptance criteria", t.acceptance_criteria),
            ("Suggested disciplines", t.suggested_disciplines),
            ("Questions for the company", t.questions_for_company),
            ("What an AI agent can do now", o.ai_can_handle),
            ("Scoping notes", o.scoping_notes),
        ):
            if items:
                lines += [f"### {heading}", ""] + [f"- {i}" for i in items] + [""]
        return "\n".join(lines).strip() + "\n"
