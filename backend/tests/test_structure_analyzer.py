import pytest

from app.services.pdf_analyzer import PageInfo, PdfDocument, Row, Span, analyze_pdf
from app.services.structure_analyzer import (
    Block,
    action_verbs,
    analyze_structure,
    classify_heading,
    extract_bullets,
    glyph_length,
    has_phone,
    is_quantified,
    split_sections,
    starts_with_action_verb,
)


class Page:
    """Builds rows top to bottom like a resume page."""

    def __init__(self):
        self.rows: list[Row] = []
        self.y = 50.0

    def add(self, text, x0=36.0, size=11.0, bold=False, gap=2.0, glyph=False) -> "Page":
        h = size * 1.15
        spans = []
        if glyph:
            spans.append(Span("•", x0, self.y - 1, x0 + 4, self.y - 1 + h, size=size))
            x0 += 18
        spans.append(Span(text, x0, self.y, x0 + len(text) * size * 0.5, self.y + h, size=size, bold=bold))
        self.rows.append(Row(spans))
        self.y += h + gap
        return self

    def heading(self, text):
        return self.add(text, size=12, bold=True, gap=4)

    def bullet(self, text):
        return self.add(text, x0=54, glyph=True)

    def doc(self, pages=1) -> PdfDocument:
        return PdfDocument(rows=self.rows, pages=[PageInfo(0, 612, 792, 3000)], page_count=pages)


# ---------------------------------------------------------------- headings


@pytest.mark.parametrize(
    "heading,key",
    [
        ("EXPERIENCE", "experience"),
        ("Professional IT Experience", "experience"),
        ("ADDITIONL WORK EXPERIENCE", "experience"),  # typo seen in a real resume
        ("TECHNICAL INTERNSHIP EXPERIENCE", "experience"),
        ("Research Experience", "experience"),
        ("LEADERSHIP EXPERIENCE", "activities"),
        ("AWARDS AND ACTIVITIES", "activities"),
        ("ACADEMIC PROJECTS", "projects"),
        ("Technical Skills:", "skills"),
        ("SKILLS & INTERESTS", "skills"),
        ("Programming Languages", "skills"),
        ("SIGNIFICANT COURSEWORK", "education"),
        ("EDUCATION:", "education"),
        ("Professional Summary", "summary"),
        ("Certifications & Licenses", "certifications"),
        ("Languages", "other"),
        ("E X P E R I E N C E", "experience"),
        (" EDUCATION", "education"),  # icon glyph in front
        ("Pricing Analyst", None),
        ("GE Transportation", None),
    ],
)
def test_classify_heading(heading, key):
    assert classify_heading(heading) == key


def test_project_titles_are_not_section_headings():
    """Bold 'X Project' titles contain a heading keyword but not the heading style."""
    page = (
        Page()
        .add("Jane Doe", size=14, bold=True)
        .heading("EDUCATION")
        .add("State University, B.S. Computer Science")
        .heading("ACADEMIC PROJECTS")
        .add("Voting Program Project", bold=True)
        .bullet("Designed and implemented a voting program")
        .heading("EXPERIENCE")
        .add("Acme Corp, Intern", bold=True)
        .bullet("Built an internal dashboard")
    )
    blocks = split_sections(page.rows)
    assert [(b.key, b.heading) for b in blocks] == [
        ("header", None),
        ("education", "EDUCATION"),
        ("projects", "ACADEMIC PROJECTS"),
        ("experience", "EXPERIENCE"),
    ]


def test_unrecognized_heading_in_same_style_becomes_other():
    page = Page().heading("EXPERIENCE").add("Acme").heading("HACKATHONS").add("Won first place")
    assert [b.key for b in split_sections(page.rows)] == ["experience", "other"]


def test_inline_label_is_not_a_heading():
    page = Page().heading("SKILLS").add("Languages: SQL, C++, Python", bold=True)
    assert [b.key for b in split_sections(page.rows)] == ["skills"]


def test_repeated_section_keys_are_all_kept():
    page = Page().heading("WORK EXPERIENCE").add("A").heading("RESEARCH EXPERIENCE").add("B")
    blocks = split_sections(page.rows)
    assert [b.key for b in blocks] == ["experience", "experience"]


# ---------------------------------------------------------------- bullets


def test_glyph_detection():
    assert glyph_length("• Built") == 1
    assert glyph_length(" Built") == 1  # Word Symbol-font private-use bullet
    assert glyph_length("- Built") == 1
    assert glyph_length("o Built") == 1
    assert glyph_length("-3 years") == 0
    assert glyph_length("obviously") == 0
    assert glyph_length("Built") == 0


def _block(page: Page, key="experience") -> Block:
    return Block(key, "EXPERIENCE", page.rows)


def test_bullets_with_wrapped_lines_and_headers():
    page = (
        Page()
        .add("Acme Corp | Chicago, IL May 2024 - Present", bold=True)
        .bullet("Developed a pipeline that processes")
        .add("two million events per day", x0=72)  # continuation at the hanging indent
        .add("skills", x0=72)  # one-word continuation (seen in a real resume)
        .bullet("Reduced costs by 30%")
        .add("Globex, Analyst Jan 2023 - Apr 2024")
        .bullet("Led a team of 4 engineers")
    )
    bullets, headers = extract_bullets(_block(page))
    assert [b.text for b in bullets] == [
        "Developed a pipeline that processes two million events per day skills",
        "Reduced costs by 30%",
        "Led a team of 4 engineers",
    ]
    assert headers == ["Acme Corp | Chicago, IL May 2024 - Present", "Globex, Analyst Jan 2023 - Apr 2024"]


def test_short_bullets_dropped():
    page = Page().bullet("Python").bullet("Built a compiler in Rust")
    bullets, _ = extract_bullets(_block(page))
    assert [b.text for b in bullets] == ["Built a compiler in Rust"]


def test_glyphless_section_splits_on_gaps():
    page = (
        Page()
        .add("Acme Corp Jan 2020 - Dec 2021", bold=True)
        .add("Built the billing system used by all customers.", gap=10)
        .add("Migrated the monolith to services, cutting deploy")
        .add("time from hours to minutes.")
    )
    bullets, _ = extract_bullets(_block(page))
    assert [b.text for b in bullets] == [
        "Built the billing system used by all customers.",
        "Migrated the monolith to services, cutting deploy time from hours to minutes.",
    ]


# ---------------------------------------------------------------- action verbs


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Led development of a web app", True),
        ("Developed and facilitated training", True),
        ("Leading a team of five", True),
        ("Successfully launched the product", True),
        ("Problem-solved to develop activities", True),
        ("Co-led the robotics team", True),
        ("Met with clients weekly", True),
        ("JobFit: built a resume analyzer", True),
        ("Planned the quarterly roadmap", True),
        ("Responsible for managing inventory", False),
        ("Assisted in design of a web application", False),
        ("Worked on an interdisciplinary team", False),
        ("Helped allow students to participate", False),
        ("Three varsity letters", False),
        ("The team shipped it", False),
    ],
)
def test_action_verbs(text, expected):
    assert starts_with_action_verb(text) is expected


def test_verb_forms_generated():
    verbs = action_verbs()
    assert {"build", "builds", "building", "built", "analyzed", "optimizing", "planned"} <= verbs


# ---------------------------------------------------------------- quantified


@pytest.mark.parametrize(
    "text",
    [
        "Cut costs by 30%",
        "Generated $2.5 million in revenue",
        "Improved throughput 3x",
        "Served 10,000 users",
        "Grew to 5k monthly users",
        "Organized 40 volunteers",
        "Managed 12 people",
        "Streamlined tradeshow process from 15-20 man hours",
        "Competed in 48-hour gaming marathon",
    ],
)
def test_quantified(text):
    assert is_quantified(text)


@pytest.mark.parametrize(
    "text",
    [
        "Deployed services to EC2 and S3",
        "Built tools in Python 3",
        "Wrote HTML5 pages",
        "Tracked COVID-19 cases",
        "Promoted GE during the 2015 Annual Meeting",
        "Worked in Q3 planning",
        "Automated reports",
    ],
)
def test_not_quantified(text):
    assert not is_quantified(text)


# ---------------------------------------------------------------- contact


def test_phone_detection():
    assert has_phone("(814) 865-5555 | jane@x.com")
    assert has_phone("+44 20 7946 0958")
    assert has_phone("814.865.5555")
    assert not has_phone("Graduated 2019 - 2021")
    assert not has_phone("GPA 3.86")


# ---------------------------------------------------------------- checklist


def _full_resume() -> Page:
    return (
        Page()
        .add("Jane Doe", size=16, bold=True)
        .add("jane@example.com | (555) 123-4567")
        .heading("EXPERIENCE")
        .add("Acme Corp Jan 2020 - Present", bold=True)
        .bullet("Built a billing service handling $2M per month")
        .bullet("Reduced page load time by 40%")
        .bullet("Led a team of 5 engineers")
        .bullet("Designed the event pipeline")
        .heading("EDUCATION")
        .add("State University, B.S. Computer Science")
        .heading("SKILLS")
        .add("Python, SQL, Docker")
    )


def test_strong_resume_scores_full_marks():
    result = analyze_structure(_full_resume().doc())
    assert {c.id: c.passed for c in result.checks} == {
        "contact": True,
        "experience_section": True,
        "education_section": True,
        "skills_section": True,
        "action_verbs": True,
        "quantified": True,
        "length": True,
        "parseable": True,
    }
    assert result.score == 1.0
    assert result.feedback == []


def test_failed_checks_produce_feedback():
    page = (
        Page()
        .add("Jane Doe", size=16, bold=True)
        .heading("EXPERIENCE")
        .bullet("Responsible for the website")
        .bullet("Assisted with support tickets")
        .bullet("Helped the sales team")
    )
    result = analyze_structure(page.doc(pages=3))
    failed = {c.id for c in result.checks if not c.passed}
    assert failed == {"contact", "education_section", "skills_section", "action_verbs", "quantified", "length"}
    assert result.score == pytest.approx(0.15 + 0.05)  # experience + parseable
    assert {f["check"] for f in result.feedback} == failed
    verbs = next(f for f in result.feedback if f["check"] == "action_verbs")
    assert '"Responsible for"' in verbs["message"]
    assert {f["severity"] for f in result.feedback} <= {"high", "medium", "low"}


def test_too_few_bullets_reported_as_undetected():
    page = Page().heading("EXPERIENCE").bullet("Built things for people")
    result = analyze_structure(page.doc())
    check = next(c for c in result.checks if c.id == "action_verbs")
    assert not check.passed
    assert "couldn't detect bullet points" in check.detail


def test_text_trapped_in_images_fails_parseable_check():
    doc = _full_resume().doc()
    doc.pages[0].big_images = [(0.6, 20)]  # big image, almost no readable text over it
    check = next(c for c in analyze_structure(doc).checks if c.id == "parseable")
    assert not check.passed and check.max_points == 5


def test_sections_json_keeps_document_order():
    result = analyze_structure(_full_resume().doc())
    assert [s["key"] for s in result.sections] == ["header", "experience", "education", "skills"]


# ---------------------------------------------------------------- real resumes


def test_fixture_resumes_have_core_sections_and_bullets(fixture_pdfs):
    for path in fixture_pdfs:
        result = analyze_structure(analyze_pdf(path.read_bytes()))
        keys = {b.key for b in result.blocks}
        assert {"experience", "education", "skills"} <= keys, (path.name, keys)
        assert len(result.bullets) >= 5, path.name
        assert all(len(b.text.split()) >= 3 for b in result.bullets)
        contact = next(c for c in result.checks if c.id == "contact")
        assert contact.passed, (path.name, contact.detail)
