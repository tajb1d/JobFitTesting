import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.services.feedback import MODEL, ClaudeFeedback, build_feedback, template_feedback

CONTEXT = {
    "job_title": "Software Engineer",
    "subscores": {"skill_coverage": 0.3, "semantic_match": 0.1, "resume_structure": 1.0},
    "matched_skills": [{"skill": "C++", "weight": 3.0}, {"skill": "Algorithms", "weight": 3.0}],
    "missing_skills": [
        {"skill": "Python", "weight": 3.0}, {"skill": "SQL", "weight": 3.0},
        {"skill": "Git", "weight": 3.0}, {"skill": "Java", "weight": 3.0},
        {"skill": "Docker", "weight": 0.5}, {"skill": "AWS", "weight": 0.5},
    ],
    "weak_requirements": [{"text": "Familiarity with SQL and relational databases", "section": "required", "similarity": 0.28}],
    "failed_structure_checks": [{"label": "At least 3 bullets include a quantified result", "detail": "1 of 15 bullets include a number."}],
}


def _item(t="missing_skill", s="high", m="If you've used Python, add it and name the project."):
    return {"type": t, "severity": s, "message": m}


VALID = {"items": [_item() for _ in range(4)] + [_item("strength", "low", "You already show C++, which this job requires.")]}


class FakeMessages:
    def __init__(self, text=None, stop_reason="end_turn", error=None):
        self.text, self.stop_reason, self.error, self.calls = text, stop_reason, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=self.text)],
        )


def claude(**kw) -> tuple[ClaudeFeedback, FakeMessages]:
    messages = FakeMessages(**kw)
    return ClaudeFeedback("key", client=SimpleNamespace(messages=messages)), messages


def test_valid_llm_feedback_is_used():
    gen, messages = claude(text=json.dumps(VALID))
    items, source = build_feedback(CONTEXT, gen)
    assert source == "llm"
    assert len(items) == 5 and items[-1]["type"] == "strength"

    call = messages.calls[0]
    assert call["model"] == MODEL == "claude-haiku-4-5-20251001"
    assert call["output_config"]["format"]["type"] == "json_schema"
    # Claude sees the structured result only.
    assert json.loads(call["messages"][0]["content"]) == CONTEXT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "not json at all"},
        {"text": json.dumps({"items": [_item()] * 4})},                       # too few
        {"text": json.dumps({"items": [_item()] * 9})},                       # too many
        {"text": json.dumps({"items": [_item()] * 6})},                       # no strength
        {"text": json.dumps({"items": [_item(t="praise")] * 6})},             # bad type
        {"text": json.dumps({"items": [_item(m="short")] * 6})},              # message too short
        {"text": json.dumps(VALID), "stop_reason": "max_tokens"},             # truncated
        {"text": json.dumps(VALID), "stop_reason": "refusal"},
        {"error": anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))},
        {"error": anthropic.APITimeoutError(request=httpx.Request("POST", "https://x"))},
        {"error": RuntimeError("anything else")},
    ],
)
def test_any_failure_falls_back_to_templates(kwargs):
    gen, _ = claude(**kwargs)
    items, source = build_feedback(CONTEXT, gen)
    assert source == "template"
    assert items == template_feedback(CONTEXT)


def test_template_feedback_follows_the_rules():
    items = template_feedback(CONTEXT)
    assert 1 <= len(items) <= 8
    assert items[-1]["type"] == "strength"
    required = [i for i in items if i["type"] == "missing_skill" and i["severity"] == "high"]
    assert len(required) == 3  # top required gaps only
    assert all("If you" in i["message"] for i in required)  # suggest, don't assume
    assert any("SQL and relational databases" in i["message"] for i in items)


def test_weak_required_qualification_outranks_weak_responsibility():
    weak = [{"text": "Bachelor's in CS", "section": "required", "similarity": 0.2},
            {"text": "Ship features weekly", "section": "responsibilities", "similarity": 0.2}]
    items = template_feedback({"weak_requirements": weak})
    severity = {i["message"].split('"')[1]: i["severity"] for i in items if i["type"] == "weak_requirement"}
    assert severity == {"Bachelor's in CS": "high", "Ship features weekly": "medium"}


def test_template_feedback_with_nothing_matched_still_has_a_strength():
    items = template_feedback({"missing_skills": [], "matched_skills": []})
    assert [i["type"] for i in items] == ["strength"]
