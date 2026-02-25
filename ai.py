# Simple heuristic "AI" utilities for the app.
# No external ML libs are used; instead, we leverage curated lexicons and rules.
# https://www.elastic.co/what-is/search-ranking
# used ai for imports and research on how to use it
# Simple fallback AI helpers used by the app. These are lightweight heuristics
# to avoid import errors and keep the app running in development environments.

from typing import List, Tuple

def extract_skill_tags(text: str) -> List[str]:
    """
    Naively extract skill-like tags from the provided text.
    This is a simple keyword presence check with de-duplication.
    """
    if not text:
        return []
    text_l = text.lower()
    candidates = [
        "python", "java", "c++", "javascript", "typescript", "react", "vue", "angular",
        "flask", "django", "fastapi", "api", "rest", "graphql",
        "sql", "mysql", "postgres", "sqlite",
        "ml", "ai", "data", "analysis", "pandas", "numpy",
        "docker", "kubernetes", "aws", "azure", "gcp", "cloud",
        "html", "css", "sass", "tailwind",
    ]
    tags = []
    seen = set()
    for w in candidates:
        if w in text_l and w not in seen:
            seen.add(w)
            tags.append(w)
    return tags

def triage_dispute(message: str) -> Tuple[int, str]:
    """
    Very basic triage. Returns (severity, suggested_resolution_text)
    severity: 1 (low) .. 3 (high)
    """
    if not message:
        return 1, "Please provide more details so we can assist you."
    m = message.lower()

    severity = 1
    if any(k in m for k in ["harass", "fraud", "threat", "illegal", "urgent", "emergency", "scam"]):
        severity = 3
    elif any(k in m for k in ["delay", "missing", "late", "not responding", "issue", "problem", "bug", "dispute"]):
        severity = 2

    if severity == 3:
        suggestion = "Escalate to admin immediately and preserve all relevant evidence and communications."
    elif severity == 2:
        suggestion = "Clarify expectations and timeline with the other party; escalate to admin if not resolved."
    else:
        suggestion = "Try to resolve via direct communication; document agreements to avoid future issues."

    return severity, suggestion


def rating_weight(
    rater_role: str,
    rater_trust: float,
    on_time: bool,
    change_requests: int,
    first_pass_success: bool
) -> float:
    """
    Heuristic AI weight for an individual review:
      - Trust influence: higher rater trust → more weight (0.7..1.3 range)
      - Timing: on-time work → +10% weight; late → -10%
      - Rework: each change request reduces weight (down to ~0.75)
      - First pass success: +8% weight
      - Role emphasis: company ratings +10%, non-company slightly down-weighted
      Output is clamped to [0.5, 1.5].
    """
    try:
        t = max(0.0, min(100.0, float(rater_trust or 0.0)))
    except Exception:
        t = 0.0

    # Trust factor 0.7..1.3
    trust_factor = 0.7 + 0.6 * (t / 100.0)

    # On-time influence
    timing_factor = 1.1 if on_time else 0.9

    # Rework penalty (cap at ~0.75)
    cr = max(0, int(change_requests or 0))
    rework_factor = max(0.75, 1.0 - min(cr, 5) * 0.05)

    # First-pass bonus
    fps_factor = 1.08 if bool(first_pass_success) else 1.0

    # Role emphasis
    role = (rater_role or "").lower()
    role_factor = 1.10 if role == "company" else 0.95

    w = trust_factor * timing_factor * rework_factor * fps_factor * role_factor
    return max(0.5, min(1.5, float(w)))
from typing import List, Tuple
import re


# Canonical skill tags and their common synonyms/variants
_SKILL_LEXICON = {
    "python": {"py", "python", "pandas", "numpy"},
    "javascript": {"js", "javascript", "node", "react", "vue"},
    "frontend": {"frontend", "ui", "ux", "css", "html", "tailwind", "bootstrap"},
    "backend": {"backend", "api", "rest", "flask", "django", "sqlalchemy"},
    "data": {"data", "analysis", "analytics", "etl", "dataset"},
    "database": {"db", "database", "sqlite", "postgres", "mysql", "sql"},
    "ml": {"ml", "machine", "learning", "model", "predict"},
    "devops": {"devops", "docker", "ci", "cd", "kubernetes"},
    "testing": {"test", "tests", "pytest", "unit", "qa"},
    "design": {"design", "wireframe", "prototype", "figma"},
}

# Dispute triage keywords for severity scoring
_DISPUTE_SEVERITY_KEYWORDS = {
    5: {"harassment", "threat", "fraud", "scam", "stolen", "illegal", "violence"},
    4: {"discrimination", "abuse", "bully", "unsafe", "privacy", "breach"},
    3: {"payment", "late", "deadline", "misled", "not paid", "unpaid", "delay"},
    2: {"miscommunication", "conflict", "quality", "revision", "misunderstanding"},
    1: {"question", "clarification", "help", "support"},
}

# Pre-crafted suggestion templates keyed by coarse topics
_DISPUTE_SUGGESTIONS = [
    ({"payment", "unpaid", "not paid", "late"}, "Propose a milestone-based payment plan and request proof of work or invoices."),
    ({"deadline", "delay", "late"}, "Agree on a revised timeline with clear checkpoints and document the change."),
    ({"quality", "revision", "changes"}, "Provide specific, actionable feedback and set an iteration limit with acceptance criteria."),
    ({"miscommunication", "misunderstanding"}, "Arrange a short call to realign expectations and summarize agreed actions in writing."),
    ({"harassment", "threat", "abuse"}, "Escalate for formal review, gather evidence, and consider suspending involved parties pending investigation."),
    ({"privacy", "breach"}, "Request immediate mitigation, rotate credentials if needed, and document the incident for compliance review."),
]


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def extract_skill_tags(text: str) -> List[str]:
    """
    Extract canonical skill tags from text using a synonym lexicon.
    """
    tokens = set(_tokenize(text))
    tags = []
    for canonical, synonyms in _SKILL_LEXICON.items():
        if tokens.intersection(synonyms):
            tags.append(canonical)
    return tags


def triage_dispute(message: str) -> Tuple[int, str]:
    """
    Returns (severity: 1-5, suggested_resolution: str) for a dispute message.
    """
    tokens = set(_tokenize(message))
    max_sev = 1
    for sev, words in _DISPUTE_SEVERITY_KEYWORDS.items():
        if tokens.intersection(words):
            max_sev = max(max_sev, sev)

    best_suggestion = "Recommend opening a dialog to clarify expectations and document next steps."
    best_hits = 0
    for keywords, suggestion in _DISPUTE_SUGGESTIONS:
        hits = len(tokens.intersection(keywords))
        if hits > best_hits:
            best_hits = hits
            best_suggestion = suggestion

    return max_sev, best_suggestion
