"""Suggested target roles for onboarding pre-fill (plan §5, step 7). Deterministic: no LLM."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.nlp_analyzer import SkillMatch

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MAX_SUGGESTIONS = 3
MIN_SCORE = 0.25
TITLE_BONUS = 0.25


@dataclass(frozen=True)
class RoleProfile:
    role: str
    title_keywords: tuple[str, ...]
    skills: dict[str, float]


@lru_cache
def load_roles() -> tuple[RoleProfile, ...]:
    raw = json.loads((DATA_DIR / "roles.json").read_text())
    return tuple(
        RoleProfile(r["role"], tuple(k.lower() for k in r["title_keywords"]), r["skills"])
        for r in raw
    )


def suggest_roles(skills: list[SkillMatch], job_titles: list[str]) -> list[str]:
    """Up to 3 roles, best first. Returns fewer (or none) rather than padding with weak
    matches.

    Score = weight of the role's signal skills found in the resume / total signal weight
    (normalized, so roles with long skill lists don't always win), plus a bonus when a past
    job title contains one of the role's title keywords.
    """
    have = {s.canonical for s in skills}
    titles = " | ".join(job_titles).lower()

    scored = []
    for role in load_roles():
        total = sum(role.skills.values())
        score = sum(w for skill, w in role.skills.items() if skill in have) / total
        if any(re.search(rf"\b{re.escape(k)}\b", titles) for k in role.title_keywords):
            score += TITLE_BONUS
        if score >= MIN_SCORE:
            scored.append((score, role.role))

    scored.sort(key=lambda s: (-s[0], s[1]))
    return [role for _, role in scored[:MAX_SUGGESTIONS]]
