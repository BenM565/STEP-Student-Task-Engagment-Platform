# Document Analysis Agent: summarises company documents, extracts key facts,
# flags risks and inconsistencies, and answers the company's questions using
# only what the documents say. Extracted facts are checked against the text.

import re
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..registry import AgentResult, BaseAgent, InputField, register_agent
from ..task_bridge import HUMAN_TASK_GUIDANCE, HumanTaskAssessment, NextStep


class DocumentAnalysisInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    questions: str = Field(default="", max_length=5000)
    focus: str = Field(default="", max_length=2000)


class DocumentSummary(BaseModel):
    filename: str
    document_type: str = Field(description="e.g. 'Supplier contract', 'Board minutes', 'Product spec'")
    summary: str
    key_points: List[str]


class KeyFact(BaseModel):
    label: str = Field(description="What the fact is, e.g. 'Contract end date', 'Monthly fee'")
    value: str = Field(description="The value copied exactly as it appears in the document")
    source: str = Field(description="Filename and page or section, e.g. 'contract.pdf, page 3'")


class DocumentRisk(BaseModel):
    description: str
    severity: Literal["low", "medium", "high"]
    source: str
    recommendation: str


class Inconsistency(BaseModel):
    description: str = Field(description="What conflicts, quoting both sides")
    sources: List[str]
    suggested_resolution: str


class Answer(BaseModel):
    question: str
    status: Literal["answered", "partially_answered", "not_found"]
    answer: str = Field(description="Answer based only on the documents; say what is missing when not fully answered")
    sources: List[str]


class DocumentAnalysisOutput(BaseModel):
    overview: str = Field(description="3-5 sentences: what these documents are and what matters most in them")
    documents: List[DocumentSummary]
    key_information: List[KeyFact]
    risks: List[DocumentRisk]
    inconsistencies: List[Inconsistency] = Field(description="Conflicts within or between documents; empty if none")
    answers: List[Answer] = Field(description="One entry per company question, in order; empty if no questions were asked")
    gaps: List[str] = Field(description="Information a reader would expect that the documents do not contain")
    recommended_next_steps: List[NextStep]
    human_task_assessment: HumanTaskAssessment


SYSTEM_PROMPT = f"""
You are the Document Analysis Agent on STEP. A company attaches documents and wants to understand them quickly and safely.

How to work:
- Use only the attached documents. Never fill gaps from general knowledge; record missing information under gaps, and mark an answer not_found when the documents do not contain it.
- Cite every fact, risk, inconsistency and answer with the filename and page (PDF text includes [Page N] markers) or the nearest heading.
- In key_information, copy values exactly as written (dates, amounts, names, durations, notice periods), so they can be checked against the source.
- Risks should be concrete consequences for this company (for example an auto-renewal clause, unlimited liability, a missing data-processing term), with a practical recommendation. Do not present legal or financial advice as definitive; recommend professional review where the stakes warrant it.
- Inconsistencies are conflicting statements, figures or dates within or across documents. Quote both sides.
- If a document is marked truncated, say that the analysis covers only part of it.

{HUMAN_TASK_GUIDANCE}
""".strip()


@register_agent
class DocumentAnalysisAgent(BaseAgent):
    slug = "document-analysis"
    name = "Document Analyst"
    tagline = "Analyse documents and extract the information, risks and answers that matter."
    description = (
        "Reads the documents you attach and produces summaries, the key facts with their source, risks, "
        "inconsistencies between or within documents, and answers to your questions based only on the text."
    )
    category = "Documents"
    icon = "file-earmark-text"
    version = "1.0.0"
    capabilities = (
        "Summaries of each document",
        "Key facts copied from the text, with page references",
        "Risks and inconsistencies",
        "Answers to your questions, or 'not found'",
        "Checks that extracted facts appear in the source",
    )
    example_use_cases = (
        "Summarise this supplier contract and flag anything risky.",
        "Do these two versions of our policy contradict each other?",
        "What does this tender document require us to submit, and by when?",
    )

    workspace_prompt = (
        "Attach the documents you want analysed. I will summarise them, pull out the key facts with page references, "
        "flag risks and inconsistencies, and answer your questions using only what the documents say."
    )
    input_fields = (
        InputField(
            name="questions",
            label="Questions about the documents",
            rows=4,
            placeholder="One per line, e.g.\nWhen does the contract renew?\nWhat is our liability cap?",
            help_text="Optional. One question per line.",
        ),
        InputField(
            name="focus",
            label="What should the analysis focus on?",
            rows=2,
            placeholder="e.g. Termination terms and anything that costs us money",
            help_text="Optional.",
        ),
    )
    run_button_label = "Analyse documents"

    input_model = DocumentAnalysisInput
    output_model = DocumentAnalysisOutput
    system_prompt = SYSTEM_PROMPT

    accepts_files = True
    allowed_extensions = frozenset({".pdf", ".docx", ".txt", ".md", ".csv"})
    min_files = 1
    max_files = 5
    files_label = "Documents to analyse"

    max_output_tokens = 16000
    result_template = "ai_agents/results/document_analysis.html"

    def build_prompt(self, data: DocumentAnalysisInput, documents) -> str:
        questions = [q.strip(" -•\t") for q in data.questions.splitlines() if q.strip(" -•\t")]
        q_text = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)) or "No specific questions."
        return (
            "Analyse the attached documents.\n\n"
            f"<company_input field=\"questions\">\n{q_text}\n</company_input>\n\n"
            f"<company_input field=\"focus\">\n{data.focus or 'No specific focus.'}\n</company_input>\n\n"
            f"Documents:\n{self.render_documents(documents)}"
        )

    def execute(self, ctx) -> AgentResult:
        result = super().execute(ctx)
        corpus = _normalise(" ".join(d.text for d in ctx.documents))
        checks = [_normalise(f.value) in corpus if _normalise(f.value) else False
                  for f in result.output.key_information]
        return AgentResult(output=result.output, artifacts={"fact_checks": checks})

    def run_title(self, data: DocumentAnalysisInput) -> str:
        for text in (data.questions, data.focus):
            for line in text.splitlines():
                if line.strip(" -•\t"):
                    return line.strip(" -•\t")[:120]
        return "Document analysis"

    def task_draft(self, output: DocumentAnalysisOutput):
        a = output.human_task_assessment
        return a.suggested_task if a.recommended else None

    def to_markdown(self, o: DocumentAnalysisOutput, artifacts=None) -> str:
        lines = ["# Document analysis", "", o.overview, ""]
        for d in o.documents:
            lines += [f"## {d.filename} ({d.document_type})", "", d.summary, ""] + [f"- {p}" for p in d.key_points] + [""]
        if o.key_information:
            lines += ["## Key information", "", "| Item | Value | Source |", "|---|---|---|"]
            lines += [f"| {_cell(f.label)} | {_cell(f.value)} | {_cell(f.source)} |" for f in o.key_information] + [""]
        if o.answers:
            lines += ["## Answers", ""]
            for a in o.answers:
                lines += [f"**{a.question}** ({a.status.replace('_', ' ')})", "", a.answer,
                          f"_Sources: {', '.join(a.sources) or 'none'}_", ""]
        if o.risks:
            lines += ["## Risks", ""] + [f"- **{r.severity.upper()}**: {r.description} ({r.source}). {r.recommendation}" for r in o.risks] + [""]
        if o.inconsistencies:
            lines += ["## Inconsistencies", ""] + [f"- {i.description} ({'; '.join(i.sources)}). {i.suggested_resolution}" for i in o.inconsistencies] + [""]
        if o.gaps:
            lines += ["## Gaps", ""] + [f"- {g}" for g in o.gaps] + [""]
        lines += ["## Recommended next steps", ""] + [f"{i}. **{s.step}** - {s.rationale}" for i, s in enumerate(o.recommended_next_steps, 1)]
        return "\n".join(lines).strip() + "\n"


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
