"""Frequent unmatched noun chunks in job requirements (plan §5), so skills.json can grow.

Reports candidates only; a person decides what to add. Ingestion-only: noun chunks need the
tagger and parser, which the web app's pipeline (nlp_analyzer.get_nlp) excludes to fit
Render's 512 MB. app.main never imports this module.

Runnable over the stored corpus: python -m app.ingestion.vocab [--top N] [--min-jobs K]"""

import argparse
import re
from collections import Counter
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
}


@lru_cache
def get_chunk_nlp() -> Language:
    """en_core_web_sm with the tagger and parser (needed for noun_chunks). Ingestion only."""
    return spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])


def _clean(chunk) -> str | None:
    tokens = list(chunk)
    while tokens and tokens[0].pos_ in _DROP_LEADING:
        tokens = tokens[1:]
    if not tokens or len(tokens) > MAX_TOKENS:
        return None
    term = re.sub(r"\s+", " ", " ".join(t.text for t in tokens)).lower()
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


def format_report(counter: Counter, top: int = 25, min_jobs: int = 3) -> str:
    frequent = [(term, n) for term, n in counter.most_common() if n >= min_jobs][:top]
    if not frequent:
        return f"  (no unmatched term appears in {min_jobs}+ jobs)"
    width = max(len(term) for term, _ in frequent)
    return "\n".join(f"  {term:<{width}}  {n} jobs" for term, n in frequent)


def main(argv: list[str] | None = None) -> None:
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Job

    parser = argparse.ArgumentParser(description="Report frequent noun chunks not in skills.json.")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--min-jobs", type=int, default=10)
    args = parser.parse_args(argv)

    with get_sessionmaker()() as db:
        rows = db.execute(select(Job.title, Job.description_text).where(Job.status == "open")).all()
    counter: Counter = Counter()
    for title, text in rows:
        counter.update(unmatched_terms(requirement_texts(parse_job(text, title).sections)))
    print(f"Frequent unmatched terms across {len(rows)} open jobs (candidates for skills.json):")
    print(format_report(counter, args.top, args.min_jobs))


if __name__ == "__main__":
    main()
