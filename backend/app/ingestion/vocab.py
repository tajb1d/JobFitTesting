"""Frequent unmatched noun chunks in job requirements (plan §5), so skills.json can grow.

Reports candidates only; a person decides what to add. Ingestion-only: noun chunks need the
tagger and parser, which the web app's pipeline (nlp_analyzer.get_nlp) excludes to fit
Render's 512 MB. app.main never imports this module.

Runnable over the stored corpus: python -m app.ingestion.vocab [--top N] [--min-companies K]"""

import argparse
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from functools import lru_cache

import spacy
from spacy.language import Language

from app.services.jd_parser import JobSection, parse_job
from app.services.nlp_analyzer import get_skill_matcher

# Sections that list what the job needs; elsewhere noun chunks are mostly company prose.
VOCAB_SECTIONS = ("required", "nice")
_DROP_LEADING = {"DET", "PRON", "NUM", "PUNCT"}
MAX_TOKENS = 4
# Generic requirement words that are never skills. Any chunk whose words all appear here is
# dropped ("strong knowledge", "years of experience" is two chunks: "years", "experience").
GENERIC_WORDS = {
    "ability", "abilities", "background", "candidate", "candidates", "company", "degree",
    "equivalent", "experience", "experiences", "expertise", "familiarity", "field", "fields",
    "knowledge", "level", "passion", "plus", "proficiency", "record", "related", "relevant",
    "requirement", "requirements", "role", "roles", "skill", "skills", "strong", "team",
    "teams", "track", "understanding", "work", "working", "year", "years", "way", "ways",
    "things", "thing", "time", "example", "examples", "opportunity", "people", "others",
    "environment", "environments", "stakeholders", "range", "variety", "who", "what",
    "bachelor", "bachelors", "master", "masters", "phd", "bs", "ms", "ba", "etc", "e.g.",
    "i.e.", "least", "ideal", "demonstrated", "proven", "excellent", "great", "good", "solid",
    "deep", "hands", "similar", "multiple", "new", "high", "best", "other", "more", "most",
    "science", "computer", "engineering", "business", "technical", "quantitative", "day",
    "detail", "details", "attention", "willingness", "comfort", "ambiguity", "success",
    "impact", "fast", "paced", "fast-paced", "environment", "communication", "written",
    "verbal", "english", "fluency", "degree", "degrees", "discipline", "disciplines",
    "industry", "customers", "customer", "product", "products", "tools", "technology",
    "on", "prior", "direct", "professional", "practical", "minimum", "preferred",
    "qualification", "qualifications", "above", "cross", "functional", "building", "part",
    "core", "information", "process", "processes", "concepts", "needs", "value", "quality",
}


@lru_cache
def get_chunk_nlp() -> Language:
    """en_core_web_sm with the tagger and parser (needed for noun_chunks). Ingestion only."""
    return spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])


def _clean(chunk) -> str | None:
    tokens = [t for t in chunk if t.tag_ != "POS"]  # possessive "'s": "bachelor's degree"
    while tokens and tokens[0].pos_ in _DROP_LEADING:
        tokens = tokens[1:]
    if not tokens or len(tokens) > MAX_TOKENS:
        return None
    # A lone lowercase common noun ("customers", "tools", "ambiguity") is almost never a
    # skill. Single words stay only when they look like names: proper nouns, acronyms,
    # capitalized or containing digits ("ITAR", "Salesforce", "S3").
    if len(tokens) == 1:
        word = tokens[0]
        if word.pos_ != "PROPN" and word.text.islower() and not any(ch.isdigit() for ch in word.text):
            return None
    # Span text keeps hyphenated words whole ("hands-on", not "hands - on").
    term = chunk.doc[tokens[0].i : tokens[-1].i + 1].text
    term = re.sub(r"\s*['’]s\b", "", re.sub(r"\s+", " ", term)).lower()
    term = term.strip(" .,;:()[]\"'•-–—/")
    if len(term) < 3 or not re.search(r"[a-z]", term):
        return None
    if set(term.replace("-", " ").split()) <= GENERIC_WORDS:
        return None
    return term


def unmatched_terms(section_texts: Iterable[str]) -> set[str]:
    """Noun chunks from one job's requirement sections that contain no known skill. A set,
    so each job counts a term once."""
    matcher = get_skill_matcher()
    terms: set[str] = set()
    for doc in get_chunk_nlp().pipe(t for t in section_texts if t.strip()):
        for chunk in doc.noun_chunks:
            term = _clean(chunk)
            if term and not matcher.find(chunk.text, "skills"):
                terms.add(term)
    return terms


def requirement_texts(sections: list[JobSection]) -> list[str]:
    return [s.text for s in sections if s.key in VOCAB_SECTIONS]


class TermStats:
    """How many jobs, and how many distinct companies, use each unmatched term. Ranked by
    companies: a real skill recurs across employers, while one company's template text
    ("ITAR" export boilerplate, "Toasters") repeats only within its own postings."""

    def __init__(self) -> None:
        self.jobs: Counter = Counter()
        self.companies: defaultdict[str, set] = defaultdict(set)

    def add(self, company: object, terms: Iterable[str]) -> None:
        for term in terms:
            self.jobs[term] += 1
            self.companies[term].add(company)

    def __bool__(self) -> bool:
        return bool(self.jobs)

    def ranked(self, min_companies: int = 2) -> list[tuple[str, int, int]]:
        rows = [(t, len(c), self.jobs[t]) for t, c in self.companies.items() if len(c) >= min_companies]
        return sorted(rows, key=lambda r: (-r[1], -r[2], r[0]))


def format_report(stats: TermStats, top: int = 25, min_companies: int = 2) -> str:
    frequent = stats.ranked(min_companies)[:top]
    if not frequent:
        return f"  (no unmatched term appears at {min_companies}+ companies)"
    width = max(len(term) for term, _, _ in frequent)
    return "\n".join(f"  {term:<{width}}  {c} companies, {n} jobs" for term, c, n in frequent)


def main(argv: list[str] | None = None) -> None:
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Job

    parser = argparse.ArgumentParser(description="Report frequent noun chunks not in skills.json.")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--min-companies", type=int, default=3)
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
        rows = db.execute(select(Job.company_id, Job.title, Job.description_text)
                          .where(Job.status == "open")).all()
    stats = TermStats()
    for company_id, title, text in rows:
        stats.add(company_id, unmatched_terms(requirement_texts(parse_job(text, title).sections)))
    print(f"Frequent unmatched terms across {len(rows)} open jobs (candidates for skills.json):")
    print(format_report(stats, args.top, args.min_companies))


if __name__ == "__main__":
    main()
