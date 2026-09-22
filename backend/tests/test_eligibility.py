import pytest

from app.services.eligibility import extract_min_years, level_from_title, years_mentions


@pytest.mark.parametrize(
    "title,level",
    [
        # intern
        ("Software Engineering Intern", "intern"),
        ("Data Science Internship - Summer 2027", "intern"),
        ("Co-op, Mechanical Engineering", "intern"),
        # entry
        ("New Grad Software Engineer", "entry"),
        ("Junior Frontend Developer", "entry"),
        ("Jr. Data Analyst", "entry"),
        ("Associate Software Engineer", "entry"),
        ("Entry-Level Business Analyst", "entry"),
        ("Graduate Engineer", "entry"),
        ("Software Engineer I", "entry"),
        # mid
        ("Software Engineer II", "mid"),
        ("Mid-Level Backend Engineer", "mid"),
        # senior
        ("Senior Software Engineer", "senior"),
        ("Sr. Product Designer", "senior"),
        ("Software Engineer III", "senior"),
        ("Senior Associate, Data", "senior"),  # senior outranks associate
        # staff+
        ("Staff Engineer", "staff"),
        ("Principal Data Scientist", "staff"),
        ("Engineering Manager, Payments", "staff"),
        ("Tech Lead, Mobile", "staff"),
        ("Manager, Recruiting - Sales", "staff"),
        ("Sales Manager", "staff"),
        ("Enterprise Architect Lead", "staff"),  # "lead" still counts
        ("Director of Engineering", "staff"),
        ("Associate Director, Analytics", "staff"),  # director outranks associate
        ("VP of Product", "staff"),
        ("Head of Data", "staff"),
        ("Senior Engineering Manager", "staff"),
        # no cue → unknown
        ("Software Engineer", None),
        ("Registered Nurse", None),
        ("Data Analyst", None),
        ("IT Support Specialist", None),  # "IT" is not the numeral I
        # IC job families named "manager"/"architect" carry no level by themselves
        ("Product Manager, Treasury for Platforms", None),
        ("Solutions Architect, (Italian fluency)", None),
        ("Events Manager", None),
        ("Partner Development Manager", None),
        ("Senior Product Manager", "senior"),
        ("Staff Product Manager, Payments", "staff"),
        ("Senior Product Manager, Growth", "senior"),
        ("NextStay Fellow, Tech Ops & Programs", None),  # a fellowship, not a Fellow
        ("Technical Fellow, AI", "staff"),
    ],
)
def test_level_from_title(title, level):
    assert level_from_title(title) == level


@pytest.mark.parametrize(
    "text,years",
    [
        ("3+ years of experience with Python", [3]),
        ("5 years experience in backend development", [5]),
        ("3-5 years of professional experience", [3]),
        ("3 to 5 years of industry experience", [3]),
        ("2–4 yrs experience", [2]),
        ("Minimum of 5 years in software engineering", [5]),
        ("At least two years of experience", [2]),
        ("two (2) years of experience", [2]),
        ("Ten plus years building distributed systems", [10]),
        ("5 or more years of hands-on experience", [5]),
        ("1 year of experience", [1]),
        ("0-2 years of experience; internships count", [0]),
        ("1.5 years of experience", [1]),
        ("seven years' experience managing teams", [7]),
        # Not about the candidate's experience:
        ("We've been in business 25 years", []),
        ("Founded over 10 years ago", []),
        ("401(k) that vests after 3 years", []),
        ("Serving customers for over 30 years", []),
        ("Over the past 10 years we grew 5x", []),
        ("Recognized as a top employer 3 years in a row", []),
        # Requires experience wording when the context is loose:
        ("Commitment of at least two years", []),
    ],
)
def test_years_with_experience_context(text, years):
    assert years_mentions(text, require_experience_context=True) == years


def test_required_section_needs_no_experience_wording():
    assert years_mentions("At least two years in a similar role", require_experience_context=False) == [2]


def test_min_years_prefers_required_sections_and_takes_the_smallest():
    sections = [
        ("intro", "Join our team of engineers with 15 years of experience combined."),
        ("required", "- 5+ years of Python\n- Bachelor's + 4 years, or Master's + 2 years of experience"),
        ("nice", "- 1 year of Go experience"),
    ]
    assert extract_min_years(sections) == 2


def test_nice_to_have_years_never_set_the_minimum():
    assert extract_min_years([("nice", "3+ years of experience with Kubernetes")]) is None


def test_falls_back_to_experience_mentions_without_required_section():
    sections = [("intro", "You bring 4+ years of experience shipping web apps."),
                ("benefits", "Our 401(k) vests after 3 years.")]
    assert extract_min_years(sections) == 4


def test_no_years_mentioned():
    assert extract_min_years([("required", "Strong SQL skills")]) is None


# ---------------------------------------------------------------- feed adjustment (plan §8)

from app.services.eligibility import eligibility_adjustment  # noqa: E402


def _adjust(job_level, min_years, user_level, show_stretch=False):
    return eligibility_adjustment(job_level, min_years, user_level, show_stretch=show_stretch,
                                  penalty=0.85, hide_levels_above=2, hide_years_margin=3)


@pytest.mark.parametrize(
    "job_level,min_years,user_level,levels_above,stretch,hidden,multiplier",
    [
        ("entry", None, "entry", 0, False, False, 1.0),
        ("intern", None, "entry", -1, False, False, 1.0),     # below: no bonus, no penalty
        ("mid", None, "entry", 1, True, False, 0.85),
        ("senior", None, "entry", 2, True, True, 0.85**2),
        ("staff", None, "mid", 2, True, True, 0.85**2),
        ("staff", None, "senior", 1, True, False, 0.85),
        (None, None, "entry", None, False, False, 1.0),       # unknown job level
        ("staff", 10, None, None, False, False, 1.0),         # unknown user level: no rules
        (None, 5, "entry", None, True, True, 1.0),            # entry max 2 + 3 <= 5
        (None, 4, "entry", None, False, False, 1.0),
        (None, 3, "intern", None, True, True, 1.0),           # intern max 0 + 3
        (None, 20, "staff", None, False, False, 1.0),         # staff has no ceiling
    ],
)
def test_eligibility_adjustment(job_level, min_years, user_level, levels_above, stretch, hidden, multiplier):
    e = _adjust(job_level, min_years, user_level)
    assert (e.levels_above, e.stretch, e.hidden) == (levels_above, stretch, hidden)
    assert e.multiplier == pytest.approx(multiplier)


def test_show_stretch_disables_hiding_but_keeps_the_penalty():
    e = _adjust("senior", 8, "entry", show_stretch=True)
    assert (e.hidden, e.stretch) == (False, True)
    assert e.multiplier == pytest.approx(0.85**2)
