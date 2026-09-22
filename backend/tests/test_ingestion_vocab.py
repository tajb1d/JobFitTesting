"""Unmatched noun-chunk report (plan §5). Uses the installed spaCy model; no network."""

from collections import Counter

from app.ingestion.vocab import format_report, requirement_texts, unmatched_terms
from app.services.jd_parser import parse_job


def test_reports_unknown_phrases_but_not_known_skills():
    terms = unmatched_terms([
        "• 3+ years of experience with Kubernetes and feature flags\n"
        "• Familiarity with the Python ecosystem\n"
        "• Experience with observability tooling"
    ])
    assert {"feature flags", "observability tooling"} <= terms
    assert not any("kubernetes" in t or "python" in t for t in terms)  # already in skills.json


def test_generic_words_and_determiners_are_dropped():
    terms = unmatched_terms(["• A strong understanding of the payment systems\n• 5+ years of experience"])
    assert "payment systems" in terms       # "the" stripped
    assert not {"experience", "years", "strong understanding"} & terms


def test_terms_count_once_per_job():
    terms = unmatched_terms(["• feature flags at scale", "• more feature flags"])
    assert "feature flags" in terms and isinstance(terms, set)


def test_only_requirement_sections_are_scanned():
    job = parse_job("About us\nWe sell loyalty programs.\nRequirements\n• Experience with feature flags\n")
    assert requirement_texts(job.sections) == ["• Experience with feature flags"]


def test_format_report_respects_min_jobs_and_top():
    counter = Counter({"feature flags": 12, "loyalty programs": 4, "rare thing": 1})
    report = format_report(counter, top=1, min_jobs=3)
    assert "feature flags" in report and "12 jobs" in report
    assert "loyalty programs" not in report  # cut by top=1
    assert "no unmatched term" in format_report(Counter({"rare": 1}), min_jobs=3)
