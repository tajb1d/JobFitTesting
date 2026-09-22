import pytest

from app.services.nlp_analyzer import extract_skills, get_skill_matcher, load_taxonomy
from app.services.pdf_analyzer import analyze_pdf
from app.services.roles import load_roles, suggest_roles
from app.services.structure_analyzer import analyze_structure

CATEGORIES = {"language", "framework", "database", "cloud", "devops", "tool", "data", "concept", "soft"}


def found(text: str, section: str = "experience") -> list[str]:
    return [e.canonical for e, _, _ in get_skill_matcher().find(text, section)]


# ---------------------------------------------------------------- taxonomy integrity


def test_taxonomy_is_valid():
    taxonomy = load_taxonomy()
    assert len(taxonomy) >= 300
    assert {e.category for e in taxonomy} <= CATEGORIES
    names = [e.canonical for e in taxonomy]
    assert len(names) == len(set(names))
    seen: dict[str, str] = {}
    for e in taxonomy:
        assert e.aliases, e.canonical
        for alias in e.aliases:
            key = alias if e.case_sensitive and alias != alias.lower() else alias.lower()
            assert key not in seen, f"{alias!r} claimed by {seen[key]} and {e.canonical}"
            seen[key] = e.canonical


def test_role_skills_exist_in_taxonomy():
    known = {e.canonical for e in load_taxonomy()}
    for role in load_roles():
        missing = set(role.skills) - known
        assert not missing, (role.role, missing)


# ---------------------------------------------------------------- matching


def test_case_insensitive_and_tokenization_variants():
    assert found("Built APIs in Node.JS, NodeJS and node.js") == ["Node.js"] * 3
    assert found("POSTGRES and PostgreSQL") == ["PostgreSQL", "PostgreSQL"]


def test_longest_match_wins():
    assert found("Wrote C++, C# and Objective-C code") == ["C++", "C#", "Objective-C"]
    assert found("Services in Spring Boot and React Native") == ["Spring", "React Native"]


def test_c_counts_only_as_a_listed_language():
    assert found("Languages: C, Python") == ["C", "Python"]
    assert found("Grade C student") == []


@pytest.mark.parametrize(
    "text",
    [
        "Excel at teamwork",
        "Interned Spring 2023",
        "Spring semester research",
        "Go to market strategy",
        "Go-getter attitude",
        "Worked with cross-functional Teams",
        "Travelled on American Express",
        "Rust belt manufacturing",
    ],
)
def test_ambiguous_words_need_context(text):
    assert found(text) == []


def test_ambiguous_words_with_context():
    assert found("Automated reports in Excel, Tableau and SQL") == ["Microsoft Excel", "Tableau", "SQL"]
    assert found("Wrote a Go service") == ["Go"]
    assert found("Built a React dashboard") == ["React"]
    assert found("Built Excel spreadsheets") == ["Microsoft Excel"]
    assert found("Deployed with Docker and Helm") == ["Docker", "Helm"]  # near another skill


def test_everything_counts_in_the_skills_section():
    assert found("Excel Word Access Go R", section="skills") == [
        "Microsoft Excel", "Microsoft Word", "Microsoft Access", "Go", "R",
    ]


def test_soft_skills_only_where_claimed():
    assert found("Strong communication and leadership") == []
    assert found("Strong communication and leadership", section="summary") == [
        "Communication", "Leadership",
    ]


def test_generic_canonical_names_do_not_match_prose():
    # "Sales", "Research", "Organization" are canonicals, not aliases.
    assert found("Increased sales at the research organization") == []


def test_extract_skills_aggregates_across_sections():
    skills = extract_skills([("experience", "Built APIs with Python and SQL"), ("skills", "Python, Docker")])
    by_name = {s.canonical: s for s in skills}
    assert by_name["Python"].count == 2 and by_name["Python"].in_skills_section
    assert by_name["SQL"].count == 1 and not by_name["SQL"].in_skills_section
    assert skills[0].canonical == "Python"  # most mentioned first


def test_fixture_resumes_have_skills(fixture_pdfs):
    for path in fixture_pdfs:
        result = analyze_structure(analyze_pdf(path.read_bytes()))
        skills = extract_skills((b.key, b.text) for b in result.blocks)
        assert len(skills) >= 5, path.name
        assert any(s.in_skills_section for s in skills), path.name


# ---------------------------------------------------------------- suggested roles


def _skills(*names):
    return extract_skills([("skills", ", ".join(names))])


def test_suggests_matching_roles_best_first():
    roles = suggest_roles(
        _skills("Python", "Django", "PostgreSQL", "REST API", "Docker", "Redis", "SQL"), []
    )
    assert roles[0] == "Backend Engineer"
    assert len(roles) <= 3


def test_title_keywords_boost_roles():
    skills = _skills("Excel", "SQL")
    assert "Pricing Analyst" not in suggest_roles(skills, [])
    assert suggest_roles(skills, ["Pricing Analyst May 2024 - Aug 2024"])[0] == "Pricing Analyst"


def test_no_padding_with_weak_matches():
    assert suggest_roles(_skills("Excel"), []) == []
