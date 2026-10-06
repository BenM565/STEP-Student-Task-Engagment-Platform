# AI -> human handoff: the shared schema for a proposed STEP student task and
# the mapping onto the existing STEP task form (title / requirements / hours).
# Any agent that can propose a student task returns a StepTaskDraft.

from typing import List

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
