"""Sections, bullets, and the structure checklist (plan §5, steps 2, 3 and 6)."""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.services.pdf_analyzer import PdfDocument, Row

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Heading keyword → canonical section. Order matters: the first rule with a matching keyword
# wins, so "ACADEMIC PROJECTS" is projects (not education) and "LEADERSHIP EXPERIENCE" is
# activities (not experience).
SECTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("projects", ("project", "projects", "portfolio")),
    ("activities", ("leadership", "volunteer", "volunteering", "activities", "activity",
                    "involvement", "extracurricular", "extracurriculars", "community", "campus")),
    ("certifications", ("certification", "certifications", "certificate", "certificates",
                        "license", "licenses", "licensure")),
    ("awards", ("award", "awards", "honor", "honors", "achievements", "recognition",
                "scholarships")),
    ("publications", ("publication", "publications", "patents", "papers")),
    ("skills", ("skills", "skill", "technologies", "competencies", "proficiencies",
                "tech stack", "toolkit", "tools", "programming languages", "expertise")),
    ("experience", ("experience", "experiences", "employment", "work history",
                    "career history", "internship", "internships", "work", "research")),
    ("education", ("education", "academic", "academics", "coursework", "courses", "training")),
    ("summary", ("summary", "profile", "objective", "about me", "about", "overview")),
    ("other", ("contact", "languages", "interests", "hobbies", "references", "personal",
               "affiliations", "memberships", "additional information")),
]
BULLET_SECTIONS = {"experience", "projects", "activities"}

_GLYPHS = set("•●▪◦‣∙·■□►▸➢➤❖✓✔⁃")
_GLYPHS_NEED_SPACE = set("-–—*>o")

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_SEASON = r"(?:spring|summer|summers|fall|autumn|winter)"
_YEAR = r"(?:(?:19|20)\d{2}|'\d{2})"
_DATE = rf"(?:(?:{_MONTH}|{_SEASON})\s*,?\s*)?{_YEAR}|\d{{1,2}}/\d{{2,4}}"
DATE_RANGE = re.compile(
    rf"(?:{_DATE})\s*(?:-|–|—|to|until)\s*(?:{_DATE}|present|current|now|today)", re.IGNORECASE
)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_CANDIDATE = re.compile(r"\+?\(?\d[\d\s().\-]{8,20}\d")

WEAK_OPENERS = (
    "responsible for", "helped", "help ", "assisted", "assist ", "aided", "worked on",
    "worked with", "duties included", "involved in", "participated in", "tasked with",
    "in charge of",
)

_QUANTIFIED = [
    re.compile(r"\d+(?:\.\d+)?\s?%"),                  # 30%
    re.compile(r"\$\s?\d"),                             # $5,000
    re.compile(r"\b\d+(?:\.\d+)?[xX]\b"),               # 3x
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),              # 10,000
    re.compile(r"\b\d+(?:\.\d+)?\s?[kKmMbB]\+?(?![A-Za-z])"),  # 5k, 2M
]
# Standalone numbers: not part of a token like EC2/S3/HTML5/COVID-19/v2.1.
_NUMBER = re.compile(r"(?<![A-Za-z\d.#+\-/])(\d+(?:\.\d+)?)\+?(?![\d.]*[A-Za-z])")
_IS_YEAR = re.compile(r"^(?:19|20)\d{2}$")

MAX_BULLETS = 80
MAX_BULLET_CHARS = 500


@dataclass
class Bullet:
    section: str
    text: str


@dataclass
class Block:
    """One heading and the rows under it. A resume can have several blocks per key
    ("Work Experience" and "Research Experience" are both experience)."""

    key: str
    heading: str | None
    rows: list[Row] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(r.text for r in self.rows)


@dataclass
class Check:
    id: str
    label: str
    passed: bool
    max_points: int
    detail: str

    @property
    def points(self) -> int:
        return self.max_points if self.passed else 0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "passed": self.passed,
            "points": self.points,
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass
class StructureResult:
    blocks: list[Block]
    bullets: list[Bullet]
    job_titles: list[str]
    checks: list[Check]
    feedback: list[dict]

    @property
    def score(self) -> float:
        """S_struct = points / 100."""
        return sum(c.points for c in self.checks) / 100

    @property
    def sections(self) -> list[dict]:
        """JSON for resumes.sections: [{key, heading, text}] in document order. A list, not
        a dict: JSONB doesn't preserve key order, and keys repeat ("Work Experience" and
        "Research Experience" are both "experience")."""
        return [{"key": b.key, "heading": b.heading, "text": b.text} for b in self.blocks]


# ---------------------------------------------------------------- entry point


def analyze_structure(doc: PdfDocument) -> StructureResult:
    blocks = split_sections(doc.rows)
    bullets: list[Bullet] = []
    titles: list[str] = []
    for block in blocks:
        if block.key in BULLET_SECTIONS:
            found, headers = extract_bullets(block)
            bullets.extend(found)
            if block.key == "experience":
                titles.extend(headers)
    bullets = bullets[:MAX_BULLETS]

    checks = run_checks(doc, blocks, bullets)
    return StructureResult(
        blocks=blocks,
        bullets=bullets,
        job_titles=titles,
        checks=checks,
        feedback=[_feedback_for(c) for c in checks if not c.passed],
    )


# ---------------------------------------------------------------- headings and sections


def normalize_heading(text: str) -> tuple[str, bool]:
    """Lowercased heading text for keyword matching, plus whether it was letter-spaced
    ("E X P E R I E N C E"), in which case word boundaries are lost."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"^[^\w]+", "", text)                  # leading icons / glyphs
    text = re.sub(r"[\s:|•\-–—.]+$", "", text)            # trailing colon, bars, dashes
    tokens = text.split()
    letter_spaced = len(tokens) >= 4 and all(len(t) == 1 for t in tokens)
    if letter_spaced:
        text = "".join(tokens)
    text = text.replace("&", " and ").replace("/", " ")
    return re.sub(r"\s+", " ", text).strip().lower(), letter_spaced


def classify_heading(text: str) -> str | None:
    norm, letter_spaced = normalize_heading(text)
    for key, keywords in SECTION_RULES:
        for kw in keywords:
            if letter_spaced:
                if kw.replace(" ", "") in norm:
                    return key
            elif re.search(rf"\b{re.escape(kw)}\b", norm):
                return key
    return None


def _could_be_heading(row: Row, body_size: float) -> bool:
    text = row.text
    if re.search(r"\d", text) or len(text) > 50:
        return False
    if re.search(r":\s*\S", text):  # "Languages: SQL, C++" is a label with content
        return False
    core = re.sub(r"[\s:|]+$", "", text)
    if not core or core[-1] in ".,;":
        return False
    words = normalize_heading(text)[0].split()
    if not 1 <= len(words) <= 6:
        return False
    return row.bold or row.all_caps or row.size >= body_size + 1


def _style(row: Row) -> tuple:
    return (round(row.size), row.bold, row.all_caps)


def _body_size(rows: list[Row]) -> float:
    sizes = Counter()
    for r in rows:
        sizes[round(r.size, 1)] += len(r.text)
    return sizes.most_common(1)[0][0] if sizes else 11.0


def split_sections(rows: list[Row]) -> list[Block]:
    """Detect headings and group rows under them. Text before the first heading is the
    header (name, contact line)."""
    body_size = _body_size(rows)
    keys: dict[int, str] = {}
    for i, row in enumerate(rows):
        if _could_be_heading(row, body_size):
            key = classify_heading(row.text)
            if key:
                keys[i] = key

    if keys:
        # Learn what this resume's headings look like from the keyword hits, then:
        # - drop keyword hits in another style ("Voting Program Project" in bold 11pt under
        #   a "PROJECTS" heading in bold caps 12pt is a project title, not a section),
        # - accept unrecognized headings ("HACKATHONS") that share the style.
        # Bold company names and job titles almost never share it, which is why this beats
        # a generic "bold = heading" rule.
        signature = Counter(_style(rows[i]) for i in keys).most_common(1)[0][0]
        keys = {i: k for i, k in keys.items() if _style(rows[i]) == signature or rows[i].all_caps}
        first = min(keys)
        for i in range(first + 1, len(rows)):
            if i not in keys and _style(rows[i]) == signature and _could_be_heading(
                rows[i], body_size
            ):
                keys[i] = "other"
    else:
        # No recognizable heading words at all: fall back to large all-caps lines.
        for i, row in enumerate(rows):
            if row.all_caps and row.size >= body_size + 2 and _could_be_heading(row, body_size):
                keys[i] = "other"

    blocks = [Block("header", None)]
    for i, row in enumerate(rows):
        if i in keys:
            blocks.append(Block(keys[i], row.text))
        else:
            blocks[-1].rows.append(row)
    return [b for b in blocks if b.rows or b.key != "header"]


# ---------------------------------------------------------------- bullets


def glyph_length(text: str) -> int:
    """Length of a leading bullet glyph (plus following space), or 0."""
    if not text:
        return 0
    c = text[0]
    rest = text[1:2]
    if c in _GLYPHS or unicodedata.category(c) == "Co":  # Co: Word's Symbol-font ""
        return 1
    if c in _GLYPHS_NEED_SPACE and rest == " ":
        if c == "o" and not text[2:3].isupper():
            return 0
        return 1
    return 0


def _is_header_row(row: Row) -> bool:
    """Job/project title lines, which end the current bullet and are never bullets."""
    text = row.text
    if DATE_RANGE.search(text):
        return True
    if row.bold or row.italic:
        return True
    return row.starts_bold and len(text.split()) <= 12 and not text.endswith(".")


@dataclass
class _Open:
    text: str
    text_x0: float
    tolerance: float
    last: Row


def extract_bullets(block: Block) -> tuple[list[Bullet], list[str]]:
    """Split a section into bullets. Returns (bullets, header rows such as job titles)."""
    rows = block.rows
    has_glyphs = any(glyph_length(r.text) for r in rows)
    margin = min((r.x0 for r in rows), default=0)
    bullets: list[Bullet] = []
    headers: list[str] = []
    cur: _Open | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            text = re.sub(r"\s+", " ", cur.text).strip()
            if len(text.split()) >= 3:
                bullets.append(Bullet(block.key, text[:MAX_BULLET_CHARS]))
        cur = None

    for row in rows:
        g = glyph_length(row.text)
        if g:
            flush()
            first = row.spans[0].text
            if len(row.spans) > 1 and len(first) == 1 and glyph_length(first + " "):
                # Glyph drawn as its own span: continuation lines align with the text span.
                cur = _Open(row.text[g:].strip(), row.spans[1].x0, 4.0, row)
            else:
                cur = _Open(row.text[g:].strip(), row.x0, 3 * row.size, row)
            continue

        if has_glyphs and cur is not None and _continues(cur, row):
            cur.text += " " + row.text
            cur.last = row
            continue

        if _is_header_row(row) or has_glyphs:
            # With glyph bullets, any unglyphed, non-continuation line is a header.
            flush()
            headers.append(row.text)
            continue

        # Glyph-less section: paragraphs split on vertical gaps and sentence breaks.
        new_paragraph = cur is None or (
            row.y0 - cur.last.y1 > 0.6 * (cur.last.y1 - cur.last.y0)
            or (
                cur.text.rstrip().endswith(".")
                and row.text[:1].isupper()
                and abs(row.x0 - margin) < 3
            )
        )
        if new_paragraph:
            flush()
            cur = _Open(row.text, row.x0, 3.0, row)
        else:
            cur.text += " " + row.text
            cur.last = row
    flush()
    return bullets, headers


def _continues(cur: _Open, row: Row) -> bool:
    if DATE_RANGE.search(row.text) or row.bold:
        return False
    if row.page != cur.last.page:
        # Page break: the first line of the next page continues the last bullet if it sits
        # at the same indent.
        return abs(row.x0 - cur.text_x0) <= cur.tolerance
    line_height = cur.last.y1 - cur.last.y0
    gap = row.y0 - cur.last.y1
    in_indent = cur.text_x0 - 3 <= row.x0 <= cur.text_x0 + cur.tolerance
    return in_indent and gap < 0.8 * line_height


# ---------------------------------------------------------------- action verbs / numbers


@lru_cache
def action_verbs() -> frozenset[str]:
    base = [
        line.strip().lower()
        for line in (DATA_DIR / "action_verbs.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    forms: set[str] = set()
    for v in base:
        forms |= {v, v + "s", v + "es", v + "ed", v + "d", v + "ing"}
        if v.endswith("e"):
            forms.add(v[:-1] + "ing")
        if v.endswith("y") and len(v) > 2 and v[-2] not in "aeiou":
            forms |= {v[:-1] + "ied", v[:-1] + "ies"}
        if re.search(r"[^aeiou][aeiou][^aeiouwxy]$", v):  # plan → planned, planning
            forms |= {v + v[-1] + "ed", v + v[-1] + "ing"}
    return frozenset(forms)


def _candidate_words(text: str) -> list[str]:
    text = re.sub(r"^[\W\d_]+", "", text)
    words = re.findall(r"[a-z][a-z'\-]*", text.lower())
    i = 0
    while i < min(2, len(words) - 1) and words[i].endswith("ly"):  # "Successfully led"
        i += 1
    candidates = words[i : i + 1]
    # "JobFit: built a..." / "Capstone — designed..."
    m = re.match(r"^((?:\S+\s+){0,4}?\S*?)\s*[:—–]\s+([A-Za-z][\w'\-]*)", text)
    if m:
        candidates.append(m.group(2).lower())
    return candidates


def starts_with_action_verb(text: str) -> bool:
    verbs = action_verbs()
    if text.lower().startswith(WEAK_OPENERS):
        return False
    for w in _candidate_words(text):
        if w in verbs or ("-" in w and w.rsplit("-", 1)[-1] in verbs):
            return True
    return False


def weak_opener(text: str) -> str | None:
    lower = text.lower()
    for opener in WEAK_OPENERS:
        if lower.startswith(opener):
            return text[: len(opener.strip())]
    return None


def is_quantified(text: str) -> bool:
    if any(p.search(text) for p in _QUANTIFIED):
        return True
    for m in _NUMBER.finditer(text):
        if _IS_YEAR.match(m.group(1)):
            continue
        # "Python 3", "Windows 10": a number right after a capitalized word that isn't the
        # bullet's first word is a version, not a result. ("Managed 12 people" still counts.)
        before = text[: m.start()].split()
        if len(before) > 1 and before[-1][:1].isupper():
            continue
        return True
    return False


def has_phone(text: str) -> bool:
    for m in _PHONE_CANDIDATE.finditer(text):
        candidate = m.group(0)
        if DATE_RANGE.search(candidate):
            continue
        if 10 <= len(re.sub(r"\D", "", candidate)) <= 15:
            return True
    return False


# ---------------------------------------------------------------- checklist


def run_checks(doc: PdfDocument, blocks: list[Block], bullets: list[Bullet]) -> list[Check]:
    text = doc.text
    keys = {b.key for b in blocks}
    links = " ".join(doc.links).lower()

    has_email = bool(EMAIL.search(text)) or "mailto:" in links
    phone = has_phone(text) or "tel:" in links
    missing = [n for n, ok in (("email address", has_email), ("phone number", phone)) if not ok]

    n = len(bullets)
    verb_hits = sum(starts_with_action_verb(b.text) for b in bullets)
    weak = [w for b in bullets if (w := weak_opener(b.text))]
    quantified = sum(is_quantified(b.text) for b in bullets)
    verb_ratio = verb_hits / n if n else 0.0

    pages = doc.page_count
    total_chars = max(len(text), 1)
    trapped_pages = [
        p.number + 1
        for p in doc.pages
        if any(over < 100 for _, over in p.big_images) or (p.char_count < 300 and p.big_images)
    ]
    table_share = sum(p.table_chars for p in doc.pages) / total_chars

    if n < 3:
        verb_detail = "We couldn't detect bullet points in your experience or projects."
    else:
        verb_detail = f"{verb_hits} of {n} bullets ({verb_ratio:.0%}) start with an action verb."
        if weak:
            common = Counter(weak).most_common(2)
            verb_detail += " Weak openers: " + ", ".join(f'"{w}" ({c}×)' for w, c in common) + "."

    return [
        Check("contact", "Email and phone present", not missing, 10,
              "Found both." if not missing else f"Missing: {' and '.join(missing)}."),
        Check("experience_section", "Experience section present", "experience" in keys, 15,
              "Found." if "experience" in keys else "No experience section detected."),
        Check("education_section", "Education section present", "education" in keys, 10,
              "Found." if "education" in keys else "No education section detected."),
        Check("skills_section", "Skills section present", "skills" in keys, 10,
              "Found." if "skills" in keys else "No skills section detected."),
        Check("action_verbs", "At least 70% of bullets start with an action verb",
              n >= 3 and verb_ratio >= 0.7, 20, verb_detail),
        Check("quantified", "At least 3 bullets include a quantified result",
              quantified >= 3, 20, f"{quantified} of {n} bullets include a number."),
        Check("length", "1–2 pages", 1 <= pages <= 2, 10,
              f"{pages} page{'s' if pages != 1 else ''}."),
        Check("parseable", "No significant text trapped in tables or images",
              not trapped_pages and table_share < 0.25, 5,
              "Text is readable." if not trapped_pages and table_share < 0.25 else
              (f"Large images with little readable text on page(s) {trapped_pages}. "
               if trapped_pages else "")
              + (f"{table_share:.0%} of the text is inside tables." if table_share >= 0.25
                 else "")),
    ]


_FEEDBACK = {
    "contact": "Add {missing} near your name so recruiters can reach you.",
    "experience_section": "Add an Experience section with your jobs, internships, or research. "
                          "Use a clear heading like \"Experience\" so it's recognized.",
    "education_section": "Add an Education section with your school, degree, and graduation date.",
    "skills_section": "Add a Skills section listing your tools and technologies. Applicant "
                      "tracking systems and recruiters scan for it.",
    "action_verbs": "Start each bullet with a strong action verb that says what you did "
                    "(Built, Analyzed, Led, Reduced). {detail}",
    "quantified": "Add numbers to show impact: percentages, dollar amounts, counts, or time "
                  "saved. Aim for at least 3 bullets with a measurable result. {detail}",
    "length": "Keep your resume to 1–2 pages. Yours is {detail}",
    "parseable": "Some content may be locked in images or tables that applicant tracking "
                 "systems can't read. Keep important text as plain text. {detail}",
}


def _feedback_for(check: Check) -> dict:
    missing = check.detail.removeprefix("Missing: ").rstrip(".")
    message = _FEEDBACK[check.id].format(detail=check.detail, missing=missing).strip()
    severity = "high" if check.max_points >= 15 else "medium" if check.max_points >= 10 else "low"
    return {"check": check.id, "severity": severity, "message": message}
