"""Unmatched noun-chunk report (plan §5). Uses the installed spaCy model; no network."""

from app.ingestion.vocab import TermStats, format_report, requirement_texts, unmatched_terms
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


def test_report_ranks_by_companies_not_jobs():
    stats = TermStats()
    for job in range(40):  # one company's template repeated across its postings
        stats.add("toast", {"toasters"})
    for company in ("a", "b", "c"):
        stats.add(company, {"feature flags"})
    stats.add("a", {"rare thing"})
    assert [t for t, _, _ in stats.ranked(min_companies=1)][:2] == ["feature flags", "toasters"]
    report = format_report(stats, top=5, min_companies=2)
    assert "feature flags  3 companies, 3 jobs" in report
    assert "toasters" not in report and "rare thing" not in report
    assert "no unmatched term" in format_report(TermStats(), min_companies=2)


def test_hyphenated_words_stay_whole():
    terms = unmatched_terms(["• Experience with event-driven pipelines"])
    assert "event-driven pipelines" in terms
