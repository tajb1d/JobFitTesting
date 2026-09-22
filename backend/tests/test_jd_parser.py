from pathlib import Path

import pytest

from app.services.jd_parser import (
    WEIGHT_DEFAULT,
    WEIGHT_NICE_ONLY,
    WEIGHT_REPEATED,
    WEIGHT_REQUIRED,
    classify_jd_heading,
    html_to_text,
    parse_job,
)

DATA = Path(__file__).parent / "data"


def test_html_to_text_keeps_structure():
    html = (
        "<h2>Requirements</h2><ul><li>3+ years of <b>Python</b></li><li>SQL &amp; Postgres</li></ul>"
        "<p>We&rsquo;re hiring.</p><script>track()</script>"
    )
    assert html_to_text(html) == "Requirements\n\n• 3+ years of Python\n\n• SQL & Postgres\n\nWe’re hiring."


def test_html_to_text_handles_entity_escaped_html():
    assert html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"


def test_plain_text_passes_through():
    assert html_to_text("Line one\n\n\n\nLine two") == "Line one\n\nLine two"


@pytest.mark.parametrize(
    "line,key",
    [
        ("Requirements", "required"),
        ("Minimum Qualifications:", "required"),
        ("What you'll need", "required"),
        ("Preferred Qualifications", "nice"),  # nice beats required
        ("Nice to have:", "nice"),
        ("## Bonus points", "nice"),
        ("What you'll do", "responsibilities"),
        ("About the role", "responsibilities"),
        ("Responsibilities", "responsibilities"),
        ("Benefits", "benefits"),
        ("About us", "about"),
        ("EXPERIENCE", "required"),
        ("Experience with Python and Django", None),  # a requirement, not a heading
        ("- Requirements gathering with stakeholders", None),  # bullet
        ("We are building the future of logistics.", None),
    ],
)
def test_classify_jd_heading(line, key):
    assert classify_jd_heading(line) == key


def test_software_engineer_posting():
    job = parse_job((DATA / "software_engineer.txt").read_text())
    assert job.title == "Software Engineer I (New Grad)"
    assert job.level == "entry"
    assert job.min_years == 0  # "0-2 years"; the 401(k) "vests after 3 years" is ignored
    assert [s.key for s in job.sections] == [
        "intro", "responsibilities", "responsibilities", "required", "nice", "benefits",
    ]
    weights = {s.canonical: s.weight for s in job.skills}
    assert weights["Python"] == WEIGHT_REQUIRED
    assert weights["C++"] == WEIGHT_REQUIRED
    assert weights["Docker"] == WEIGHT_NICE_ONLY
    assert {r.section for r in job.requirements} == {"required", "responsibilities", "nice"}
    assert not any("401(k)" in r.text for r in job.requirements)  # benefits skipped


def test_nursing_posting_has_no_tech_skills_but_has_requirements():
    job = parse_job((DATA / "registered_nurse.txt").read_text())
    assert job.min_years == 2  # "Minimum of two (2) years"; "over 60 years" ignored
    assert job.skills == []
    assert len(job.requirements) >= 10


def test_skill_weight_rules():
    text = (
        "We build our platform with Kafka and more Kafka.\n"
        "Our reports use Tableau.\n"
        "Requirements\n- Python\n"
        "Nice to have\n- Docker\n"
    )
    weights = {s.canonical: s.weight for s in parse_job(text).skills}
    assert weights == {
        "Python": WEIGHT_REQUIRED,
        "Apache Kafka": WEIGHT_REPEATED,
        "Tableau": WEIGHT_DEFAULT,
        "Docker": WEIGHT_NICE_ONLY,
    }


def test_soft_skills_are_not_scored():
    job = parse_job("Requirements\n- Excellent communication and leadership skills\n- SQL\n")
    assert [s.canonical for s in job.skills] == ["SQL"]


def test_unstructured_posting_falls_back_to_bullets_then_sentences():
    bullets = parse_job("We need someone great.\n• Build data pipelines in Python\n• Own dashboards end to end\n")
    assert [r.text for r in bullets.requirements] == [
        "Build data pipelines in Python", "Own dashboards end to end",
    ]
    prose = parse_job("You will build data pipelines. You will own reporting for the sales team.")
    assert [r.text for r in prose.requirements] == [
        "You will build data pipelines.", "You will own reporting for the sales team.",
    ]


def test_explicit_title_overrides_inferred():
    job = parse_job("Great opportunity at a startup\nRequirements\n- Python", title="Senior Data Engineer")
    assert job.title == "Senior Data Engineer"
    assert job.level == "senior"
