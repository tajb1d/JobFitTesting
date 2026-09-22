"""Analysis feedback via Claude (plan §6). The only module that talks to Anthropic.

Claude sees the *structured* result (skills, weak requirements, failed checks, subscores),
never the raw resume. Any failure (API error, refusal, truncation, invalid or off-spec JSON)
falls back to template messages built from the same result, so an analysis never fails
because of the LLM.
"""

import json
import logging
from functools import lru_cache
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MIN_ITEMS, MAX_ITEMS = 5, 8

ItemType = Literal["missing_skill", "weak_requirement", "structure", "strength"]
Severity = Literal["high", "medium", "low"]


class FeedbackItem(BaseModel):
    type: ItemType
    severity: Severity
    message: str = Field(min_length=10, max_length=500)


class FeedbackList(BaseModel):
    items: list[FeedbackItem] = Field(min_length=MIN_ITEMS, max_length=MAX_ITEMS)


# Structured outputs guarantee this JSON shape; count and "at least one strength" rules are
# checked after parsing.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ItemType.__args__)},
                    "severity": {"type": "string", "enum": list(Severity.__args__)},
                    "message": {"type": "string"},
                },
                "required": ["type", "severity", "message"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are a career coach reviewing how well a candidate's resume matches one job posting. \
You receive the structured result of an automated comparison, not the resume itself: skills \
the job asks for that the resume shows or lacks (weight 3 = required, 2 = mentioned often, \
1 = mentioned, 0.5 = nice to have), job requirements the resume covers weakly, failed resume \
formatting checks, and subscores between 0 and 1.

Write 5 to 8 feedback items for the candidate.

Rules:
- Suggest additions the candidate may genuinely have. Frame missing skills conditionally, \
e.g. "If you've used Docker, add it to your skills and name the project where you used it."
- Never assume or invent experience, and never tell the candidate to claim anything they \
haven't done.
- Never rewrite the resume or draft bullet points for them.
- Prioritize by impact: required skills (weight 3) and weak required qualifications first.
- Include at least one item of type "strength" that names something specific that matches.
- Keep each message to one or two sentences, addressed to the candidate as "you".
- Use type "missing_skill", "weak_requirement", "structure", or "strength", and severity \
"high", "medium", or "low" (strengths are "low")."""


class FeedbackError(Exception):
    """The LLM response was unusable. Callers fall back to template feedback."""


class FeedbackGenerator(Protocol):
    def generate(self, context: dict) -> list[FeedbackItem]:
        """Validated feedback items, or raise FeedbackError (or an SDK error)."""
        ...


class ClaudeFeedback:
    def __init__(self, api_key: str, client: anthropic.Anthropic | None = None):
        # Short timeout and one retry: feedback sits on the request path, and the template
        # fallback is always available.
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=20.0, max_retries=1)

    def generate(self, context: dict) -> list[FeedbackItem]:
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(context, indent=2)}],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        if response.stop_reason != "end_turn":
            raise FeedbackError(f"stop_reason={response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            items = FeedbackList.model_validate_json(text).items
        except ValidationError as e:
            raise FeedbackError(f"invalid feedback JSON: {e.error_count()} errors") from e
        if not any(i.type == "strength" for i in items):
            raise FeedbackError("no strength item")
        return items


@lru_cache
def get_feedback_generator() -> FeedbackGenerator:
    """FastAPI dependency. Tests override it with a fake."""
    return ClaudeFeedback(get_settings().anthropic_api_key)


def build_feedback(context: dict, generator: FeedbackGenerator) -> tuple[list[dict], str]:
    """Feedback items for an analysis and their source ("llm" or "template"). Never raises."""
    try:
        items = generator.generate(context)
        return [i.model_dump() for i in items], "llm"
    except (anthropic.APIError, FeedbackError) as e:
        logger.warning("LLM feedback unavailable, using templates: %s", e)
    except Exception:  # never fail an analysis because of feedback
        logger.exception("Unexpected feedback error, using templates")
    return template_feedback(context), "template"


# ---------------------------------------------------------------- template fallback


def _skill_list(skills: list[dict]) -> str:
    names = [s["skill"] for s in skills]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def template_feedback(context: dict) -> list[dict]:
    """Deterministic feedback from the same structured result the LLM sees. Follows the same
    rules: suggest, don't invent; at least one strength; at most 8 items."""
    items: list[dict] = []
    missing = sorted(context.get("missing_skills", []), key=lambda s: -s["weight"])
    matched = sorted(context.get("matched_skills", []), key=lambda s: -s["weight"])

    required_missing = [s for s in missing if s["weight"] >= 3][:3]
    for s in required_missing:
        items.append({
            "type": "missing_skill",
            "severity": "high",
            "message": f"This job requires {s['skill']}. If you've used it, add it to your "
                       f"skills and mention the project or role where you used it.",
        })
    other_missing = [s for s in missing if s["weight"] < 3][:3]
    if other_missing:
        items.append({
            "type": "missing_skill",
            "severity": "medium" if any(s["weight"] >= 2 for s in other_missing) else "low",
            "message": f"The posting also mentions {_skill_list(other_missing)}. If you have "
                       f"experience with any of these, list them.",
        })
    for w in context.get("weak_requirements", [])[:2]:
        items.append({
            "type": "weak_requirement",
            "severity": "high" if w.get("section") == "required" else "medium",
            "message": f'Your resume doesn\'t clearly address: "{w["text"]}". If you\'ve done '
                       f"related work, describe it in a bullet with the result you achieved.",
        })
    for c in context.get("failed_structure_checks", [])[:2]:
        items.append({
            "type": "structure",
            "severity": "medium",
            "message": f"Resume check not met: {c['label']}. {c['detail']}",
        })

    if matched:
        strength = (f"You already show {_skill_list(matched[:3])}, which "
                    f"{'this job asks for' if matched[0]['weight'] >= 3 else 'the posting mentions'}.")
    else:
        strength = "Your resume is clearly structured, which makes it easy for recruiters to scan."
    items = items[: MAX_ITEMS - 1]
    items.append({"type": "strength", "severity": "low", "message": strength})
    return items
