# AI -> human handoff: the shared schema for a proposed STEP student task and
# the mapping onto the existing STEP task form (title / requirements / hours).
# Any agent that can propose a student task returns a StepTaskDraft.

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# Matches the existing tasks.title column length
TASK_TITLE_MAX = 200
# Guard rails for the AI's effort estimate before it reaches the form
MIN_HOURS, MAX_HOURS = 1, 400


class Milestone(BaseModel):
    name: str = Field(description="Short milestone name")
    description: str = Field(description="What is delivered or decided at this milestone")


class StepTaskDraft(BaseModel):
    title: str = Field(description="Concise, specific task title a student would recognise (under 100 characters)")
    description: str = Field(description="2-4 sentence description of the work and why the company needs it")
    objectives: List[str] = Field(description="Outcomes the task must achieve")
    required_skills: List[str] = Field(description="Concrete skills or tools the student needs")
    deliverables: List[str] = Field(description="Tangible artefacts the student hands over")
    milestones: List[Milestone] = Field(description="Ordered checkpoints from start to handover")
    acceptance_criteria: List[str] = Field(description="Checkable conditions the company will use to accept the work")
    estimated_hours: int = Field(description="Realistic total effort in hours for one university student")
    suggested_disciplines: List[str] = Field(description="Degree disciplines best suited, e.g. 'Business Information Systems'")
    questions_for_company: List[str] = Field(description="What the company must answer before a student can start")


class NextStep(BaseModel):
    step: str
    owner: Literal["company", "ai_agent", "student"] = Field(
        description="company = needs the company's decision or access; ai_agent = further analysis an AI agent can do; student = hands-on work suited to a STEP student"
    )
    rationale: str


class HumanTaskAssessment(BaseModel):
    # Shared by every agent that can hand remaining work to a STEP student
    recommended: bool = Field(description="True if part of the remaining work should be handed to a STEP student")
    rationale: str = Field(description="Why a student is or is not the right next step, referencing the specific work")
    suggested_task: Optional[StepTaskDraft] = Field(
        description="A ready-to-post STEP task when recommended is true, otherwise null"
    )


# Guidance reused in agent prompts so every agent makes the human-handoff call the same way
HUMAN_TASK_GUIDANCE = """
Deciding on a STEP student task:
STEP students are university students working remotely for a bounded number of hours. Recommend a student task when concrete hands-on work remains after your analysis that a student can do well, such as user research and interviews, UX design and prototyping, building a well-scoped feature or prototype, data collection or cleaning, testing, primary market research, or content production.
Do not recommend one when the next step is mainly a company decision, needs privileged access to production systems or sensitive personal data without supervision, or is too large or open-ended to scope (in that case recommend a smaller first phase a student could do).
When you recommend a task, scope it for one student: concrete deliverables, milestones, acceptance criteria the company can check, and an honest hour estimate. Put anything the company must clarify first in questions_for_company.
""".strip()


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items if str(item).strip())


def draft_to_task_form(draft: StepTaskDraft) -> dict:
    """Map a draft onto the existing add_task form fields.

    The existing Task model only has title, requirements and estimated_hours, so the
    structured sections are composed into the requirements text. The company edits
    everything in the normal STEP form before anything is published.
    """
    sections = [draft.description.strip()]

    def add(heading, body):
        if body:
            sections.append(f"{heading}:\n{body}")

    add("Objectives", _bullets(draft.objectives))
    add("Deliverables", _bullets(draft.deliverables))
    add("Milestones", _bullets(f"{m.name} - {m.description}" for m in draft.milestones))
    add("Acceptance criteria", _bullets(draft.acceptance_criteria))
    add("Required skills", ", ".join(s for s in draft.required_skills if s.strip()))
    add("Suited to students studying", ", ".join(d for d in draft.suggested_disciplines if d.strip()))
    add("Questions to resolve before starting", _bullets(draft.questions_for_company))

    hours = draft.estimated_hours
    if hours is not None:
        hours = max(MIN_HOURS, min(MAX_HOURS, int(hours)))

    return {
        "title": draft.title.strip()[:TASK_TITLE_MAX],
        "requirements": "\n\n".join(s for s in sections if s),
        "estimated_hours": hours,
    }
