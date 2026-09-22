"""Job description parsing (plan §6): HTML → text, sections, requirement bullets, weighted
skills, and eligibility signals. The same parser serves pasted descriptions (at analysis
time) and corpus jobs (at ingestion)."""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.services.eligibility import extract_min_years, level_from_title
from app.services.nlp_analyzer import get_skill_matcher
from app.services.structure_analyzer import glyph_length

MAX_REQUIREMENTS = 40

# Heading phrase → section key. First matching rule wins, so "Preferred Qualifications" is
# nice-to-have (not required) and "About the role" is responsibilities (not company info).
JD_SECTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("nice", ("nice to have", "nice-to-have", "preferred", "bonus", "pluses", "a plus",
              "good to have", "desired", "extra credit")),
    ("required", ("requirements", "required", "qualifications", "what you'll need",
                  "what you need", "what we're looking for", "what we are looking for",
                  "you have", "you'll have", "who you are", "must have", "must-haves",
                  "skills and experience", "your background", "about you", "experience")),
    ("responsibilities", ("responsibilities", "what you'll do", "what you will do", "the role",
                          "your role", "day to day", "day-to-day", "duties", "in this role",
                          "what you'll be doing", "job description", "key tasks",
                          "the opportunity")),
    ("benefits", ("benefits", "perks", "compensation", "salary", "pay", "what we offer",
                  "why join", "why you'll love")),
    ("about", ("about us", "about the company", "who we are", "our mission", "about the team",
               "equal opportunity", "eeo", "our company", "company overview")),
]
REQUIREMENT_SECTIONS = ("required", "responsibilities", "nice")  # priority order when capping
_IGNORED_FOR_REQUIREMENTS = {"about", "benefits"}

# Skill weights (plan §6).
WEIGHT_REQUIRED = 3.0
WEIGHT_REPEATED = 2.0
WEIGHT_DEFAULT = 1.0
WEIGHT_NICE_ONLY = 0.5


@dataclass
class JobSection:
    key: str
    heading: str | None
    text: str


@dataclass
class Requirement:
    section: str
    text: str


@dataclass
class JobSkill:
    canonical: str
    category: str
    weight: float
    count: int
    sections: set[str] = field(default_factory=set)


@dataclass
class ParsedJob:
    title: str | None
    text: str
    sections: list[JobSection]
    requirements: list[Requirement]
    skills: list[JobSkill]
    level: str | None
    min_years: int | None


# ---------------------------------------------------------------- HTML → text


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
              "section", "article", "header", "footer", "table", "blockquote", "pre"}
    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    """Plain text from HTML (or already-plain text). Handles entity-escaped HTML, which
    some job boards return (e.g. "&lt;p&gt;")."""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    if re.search(r"<(p|div|br|li|ul|ol|h[1-6]|span|strong|b|em|table)\b", raw, re.I):
        parser = _TextExtractor()
        parser.feed(raw)
        parser.close()
        raw = "".join(parser.parts)
    else:
        raw = html.unescape(raw)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- sections


def _normalize_heading(line: str) -> str:
    line = re.sub(r"^[#*_\s]+|[#*_\s:]+$", "", line)
    line = line.replace("&", " and ").replace("’", "'")
    return re.sub(r"\s+", " ", line).strip().lower()


def classify_jd_heading(line: str) -> str | None:
    """Section key if this line is a section heading, else None.

    A line containing a keyword isn't enough ("Experience with Python" is a requirement, not
    a heading). It must end with a colon, be marked up (markdown/bold/caps), or be mostly the
    keyword phrase itself."""
    stripped = line.strip()
    if not stripped or glyph_length(stripped) or len(stripped) > 80:
        return None
    norm = _normalize_heading(stripped)
    words = norm.split()
    if not 1 <= len(words) <= 8 or norm.endswith("."):
        return None
    marked = (
        stripped.endswith(":")
        or stripped.startswith(("#", "**"))
        or (stripped.isupper() and len(words) <= 6)
    )
    for key, phrases in JD_SECTION_RULES:
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", norm):
                covers = len(phrase.split()) / len(words)
                if marked or covers >= 0.5:
                    return key
    return None


def split_jd_sections(text: str) -> list[JobSection]:
    sections = [JobSection("intro", None, "")]
    body: list[list[str]] = [[]]
    for line in text.splitlines():
        key = classify_jd_heading(line)
        if key:
            sections.append(JobSection(key, line.strip().rstrip(":").strip("#* "), ""))
            body.append([])
        else:
            body[-1].append(line)
    for section, lines in zip(sections, body, strict=True):
        section.text = "\n".join(lines).strip()
    return [s for s in sections if s.text or s.heading]


def _infer_title(sections: list[JobSection]) -> str | None:
    intro = sections[0].text if sections and sections[0].key == "intro" else ""
    first = next((line.strip() for line in intro.splitlines() if line.strip()), "")
    if first and len(first.split()) <= 10 and not first.endswith((".", "!", "?", ":")):
        return first
    return None


# ---------------------------------------------------------------- requirements


def _split_units(text: str, always_split_sentences: bool = False) -> list[str]:
    """Bullet lines; long paragraphs (or all prose, if asked) are split into sentences."""
    units = []
    for line in text.splitlines():
        line = line.strip()
        g = glyph_length(line)
        if g:
            line = line[g:].strip()
        if not line or line.endswith(":"):
            continue
        if always_split_sentences or len(line.split()) > 40:
            units.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line))
        else:
            units.append(line)
    return [u for u in units if len(u.split()) >= 3]


def extract_requirements(sections: list[JobSection]) -> list[Requirement]:
    found = {key: [] for key in REQUIREMENT_SECTIONS}
    for s in sections:
        if s.key in found:
            found[s.key].extend(_split_units(s.text))
    if not any(found.values()):
        # No recognizable sections: fall back to bullet-like lines, then to all sentences,
        # skipping company boilerplate.
        pool = [s for s in sections if s.key not in _IGNORED_FOR_REQUIREMENTS]
        bullets = [
            line.strip()[glyph_length(line.strip()):].strip()
            for s in pool
            for line in s.text.splitlines()
            if glyph_length(line.strip())
        ]
        found["required"] = [b for b in bullets if len(b.split()) >= 3] or [
            u for s in pool for u in _split_units(s.text, always_split_sentences=True)
        ]
    result = [Requirement(key, text) for key in REQUIREMENT_SECTIONS for text in found[key]]
    return result[:MAX_REQUIREMENTS]


# ---------------------------------------------------------------- skills


def extract_job_skills(sections: list[JobSection]) -> list[JobSkill]:
    """Skills with plan §6 weights: 3.0 if in a required/qualifications section; 0.5 if only
    under nice-to-have; 2.0 if mentioned 2+ times; else 1.0. Soft skills are left out: nearly
    every posting lists them, and resumes rarely name them outside a summary."""
    matcher = get_skill_matcher()
    skills: dict[str, JobSkill] = {}
    for s in sections:
        # Requirement lists are skill lists, so ambiguous words ("Go", "React") count there.
        context = "skills" if s.key in ("required", "nice") else "experience"
        for entry, _, _ in matcher.find(s.text, context):
            if entry.category == "soft":
                continue
            skill = skills.setdefault(
                entry.canonical, JobSkill(entry.canonical, entry.category, 0.0, 0)
            )
            skill.count += 1
            skill.sections.add(s.key)
    for skill in skills.values():
        if "required" in skill.sections:
            skill.weight = WEIGHT_REQUIRED
        elif skill.sections == {"nice"}:
            skill.weight = WEIGHT_NICE_ONLY
        elif skill.count >= 2:
            skill.weight = WEIGHT_REPEATED
        else:
            skill.weight = WEIGHT_DEFAULT
    return sorted(skills.values(), key=lambda k: (-k.weight, -k.count, k.canonical.lower()))


# ---------------------------------------------------------------- entry point


def parse_job(description: str, title: str | None = None) -> ParsedJob:
    text = html_to_text(description)
    sections = split_jd_sections(text)
    title = (title or "").strip() or _infer_title(sections)
    return ParsedJob(
        title=title,
        text=text,
        sections=sections,
        requirements=extract_requirements(sections),
        skills=extract_job_skills(sections),
        level=level_from_title(title) if title else None,
        min_years=extract_min_years((s.key, s.text) for s in sections),
    )
