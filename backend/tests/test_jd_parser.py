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
        # Seen on real Greenhouse/Lever boards.
        ("What you should have", "required"),
        ("Our Vision of You:", "required"),
        ("Your Daily Adventures Will Include:", "responsibilities"),
        ("About Outreach", "about"),  # company blurb
        ("Why Discord?", "about"),
        ("About you", "required"),  # earlier rules beat the bare "about" fallback
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


def test_company_blurb_and_benefits_skills_are_not_scored():
    text = ("About Acme\nCustomers like Databricks and SAP trust us.\n"
            "Requirements\n- SQL\nBenefits\n- Free Tableau training\n")
    assert [s.canonical for s in parse_job(text).skills] == ["SQL"]


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


def test_trailing_legal_and_pay_boilerplate_is_not_a_requirement():
    text = (
        "Requirements\n• 3+ years of Python\n• Experience with SQL databases\n"
        "The base salary range for this position is $120,000 - $150,000.\n"
        "• Medical, dental, and vision coverage for you and your family\n"
        "Acme is an equal opportunity employer. All qualified applicants will receive "
        "consideration without regard to race, color, religion or sexual orientation.\n"
    )
    job = parse_job(text)
    assert [r.text for r in job.requirements] == ["3+ years of Python", "Experience with SQL databases"]
    assert [s.key for s in job.sections] == ["required", "benefits"]


def test_salary_line_above_responsibilities_does_not_swallow_them():
    # Chime-style: pay sentence mid-intro, then an unrecognized lead-in to real bullets.
    text = (
        "About the Role\nYou will own credit risk strategy for our lending product.\n"
        "The base salary offered for this role will begin at $109,000 and up to $140,000.\n"
        "In this role, you can expect to\n"
        "• Design and analyze A/B tests on credit limits\n• Build dashboards for loss rates\n"
    )
    job = parse_job(text)
    texts = [r.text for r in job.requirements]
    assert "Design and analyze A/B tests on credit limits" in texts
    assert "Build dashboards for loss rates" in texts
    assert not any("salary" in t for t in texts)


def test_boilerplate_words_inside_a_requirement_bullet_list_stay_put_after_a_heading():
    job = parse_job("Requirements\n• Able to lift 25 lbs\n\nBenefits\n• 401(k) match\n")
    assert [r.text for r in job.requirements] == ["Able to lift 25 lbs"]
