"""Job eligibility signals (plan §8): seniority level from the title and the minimum years of
experience the description requires. Pure functions, used at analysis time and ingestion."""

import re
from collections.abc import Iterable

Level = str  # "intern" | "entry" | "mid" | "senior" | "staff"
LEVELS: tuple[Level, ...] = ("intern", "entry", "mid", "senior", "staff")

# Checked in this order; the first rule that matches wins. The order resolves titles with
# several cues: "Associate Director" is staff, "Senior Associate" is senior,
# "Associate Software Engineer" is entry, "Engineering Manager" is staff.
_INTERN = re.compile(r"\b(intern|internship|co-?op|apprentice(ship)?)\b", re.I)
_STAFF_STRONG = re.compile(
    r"\b(principal|staff|director|vp|svp|evp|vice president|head of|chief|distinguished|"
    r"technical fellow)\b",
    re.I,
)
_SENIOR = re.compile(r"\b(senior|sr)\b\.?", re.I)
_JUNIOR = re.compile(
    r"\b(junior|jr|new grad(uate)?|graduate|entry[- ]level|early[- ]career|associate|trainee)\b",
    re.I,
)
_STAFF_WEAK = re.compile(r"\b(lead|manager|architect)\b", re.I)
# "Manager"/"Architect" in these titles names an individual-contributor job family, not a
# people manager or senior architect ("Product Manager", "Solutions Architect"), so it gives
# no level cue. People-management titles ("Engineering Manager", "Manager, Recruiting",
# "Sales Manager") still count.
_IC_ROLE = re.compile(
    r"\b(product|program|project|account|marketing|events?|partner|partnerships?|community|"
    r"customer success|success|content|brand|channel|campaign|category|solutions?|engagement|"
    r"territory|development|relations|growth|lifecycle|communications|payroll|office|"
    r"facilities|release|delivery|implementation|enterprise|cloud|data|security)\s+"
    r"(manager|architect)\b",
    re.I,
)
# Roman numerals are case-sensitive so "I" doesn't match the pronoun or "i".
_NUMERAL = re.compile(r"(?<![\w-])(I{1,3}|IV|V)(?![\w-])")
_MID = re.compile(r"\b(mid[- ]?level|intermediate)\b", re.I)


def level_from_title(title: str) -> Level | None:
    """Seniority from a job title, or None when the title gives no cue (callers treat None
    as unknown, not as mid)."""
    t = title.strip()
    if _INTERN.search(t):
        return "intern"
    if _STAFF_STRONG.search(t):
        return "staff"
    senior = bool(_SENIOR.search(t))
    if _JUNIOR.search(t) and not senior:
        return "entry"
    if _STAFF_WEAK.search(_IC_ROLE.sub(" ", t)):
        return "staff"
    if senior:
        return "senior"
    numerals = _NUMERAL.findall(t)
    if numerals:
        n = numerals[-1]
        return {"I": "entry", "II": "mid", "III": "senior", "IV": "senior", "V": "staff"}[n]
    if _MID.search(t):
        return "mid"
    return None


_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_NUM = r"(\d{1,2}(?:\.\d)?|" + "|".join(_WORDS) + r")"
# "3+ years", "3-5 years", "3 to 5 yrs", "two (2) years", "5 or more years", "10 plus years"
_YEARS = re.compile(
    rf"{_NUM}\s*(?:\(\s*\d{{1,2}}\s*\+?\s*\))?\s*(?:\+|plus|or more|or greater)?"
    rf"\s*(?:(?:-|–|—|to)\s*{_NUM}\s*\+?\s*)?(?:years?|yrs?)\b",
    re.I,
)
# Text right after the match that shows the years are about the candidate's experience.
_EXPERIENCE_AFTER = re.compile(
    r"^[\s'’]*(?:of\s+)?(?:\w+[\s,/-]+){0,6}?(?:experience|exp\b|professional|industry|"
    r"working|hands-on|developing|building|programming|engineering|in\s+(?:a|an)\s)",
    re.I,
)
# Years that describe something else: the company, benefits, history.
_NOT_EXPERIENCE = re.compile(
    r"^[\s'’]*(?:old|ago|of\s+(?:history|growth|service|operation|excellence)|in\s+business|"
    r"vesting|vest|warranty|anniversary|running|straight|in\s+a\s+row)",
    re.I,
)
_NOT_EXPERIENCE_BEFORE = re.compile(
    r"(founded|established|since|after|every|in\s+business|been\s+around|serving|operating|"
    r"history\s+of|for\s+over|over\s+the\s+(?:last|past))\s*$",
    re.I,
)


def _to_number(token: str) -> float:
    token = token.lower()
    return float(_WORDS[token]) if token in _WORDS else float(token)


def years_mentions(text: str, require_experience_context: bool) -> list[int]:
    """Minimum years for each experience mention in the text ("3-5 years" → 3)."""
    found = []
    for m in _YEARS.finditer(text):
        after = text[m.end() : m.end() + 80]
        before = text[max(0, m.start() - 25) : m.start()]
        if _NOT_EXPERIENCE.match(after) or _NOT_EXPERIENCE_BEFORE.search(before):
            continue
        if require_experience_context and not _EXPERIENCE_AFTER.match(after):
            continue
        value = _to_number(m.group(1))
        if 0 <= value <= 30:
            found.append(int(value))
    return found


def extract_min_years(sections: Iterable[tuple[str, str]]) -> int | None:
    """Smallest years-of-experience requirement (plan §8), from (section_key, text) pairs as
    produced by jd_parser. Required-section mentions win; "nice to have" years never set the
    minimum. Without a required section, any mention followed by experience wording counts."""
    sections = list(sections)
    required = [y for key, text in sections if key == "required"
                for y in years_mentions(text, require_experience_context=False)]
    if required:
        return min(required)
    elsewhere = [y for key, text in sections if key != "nice"
                 for y in years_mentions(text, require_experience_context=True)]
    return min(elsewhere) if elsewhere else None
