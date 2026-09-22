import numpy as np
import pytest

from app.config import Settings
from app.services.jd_parser import JobSkill, ParsedJob, Requirement
from app.services.matcher import ResumeForScoring, combine, score, semantic_score, skill_score, tfidf_score

SETTINGS = Settings(
    database_url="", supabase_url="", sem_baseline=0.5, weak_requirement_margin=0.1,
    weight_skill=0.5, weight_semantic=0.35, weight_structure=0.15,
)


def job(skills=(), requirements=(), text="job text"):
    return ParsedJob(
        title="T", text=text, sections=[],
        requirements=[Requirement(sec, t) for sec, t in requirements],
        skills=[JobSkill(name, "language", w, 1) for name, w in skills],
        level=None, min_years=None,
    )


def resume(skills=(), bullets=None, vector=None, structure=1.0, text="resume text"):
    return ResumeForScoring(
        text=text, skills=set(skills),
        bullet_vectors=np.array(bullets) if bullets is not None else np.empty((0, 0)),
        resume_vector=None if vector is None else np.array(vector),
        structure_score=structure,
    )


def test_skill_score_is_weighted_coverage():
    j = job(skills=[("Python", 3.0), ("SQL", 3.0), ("Docker", 0.5), ("Kafka", 1.0)])
    s, matched, missing = skill_score(j, {"Python", "Docker"})
    assert s == pytest.approx(3.5 / 7.5)
    assert [m["skill"] for m in matched] == ["Python", "Docker"]
    assert [m["skill"] for m in missing] == ["SQL", "Kafka"]


def test_skill_score_null_without_job_skills():
    assert skill_score(job(), {"Python"})[0] is None


def test_semantic_score_uses_best_bullet_per_requirement_and_rescales():
    reqs = [("required", "A"), ("required", "B")]
    # Requirement A matches bullet 0 exactly (1.0); B is 60° from its best bullet (0.5).
    req_vecs = np.array([[1.0, 0.0], [0.5, np.sqrt(3) / 2]])
    bullets = [[1.0, 0.0], [1.0, 0.0]]
    s, raw, weak = semantic_score(job(requirements=reqs), req_vecs, resume(bullets=bullets), 0.5, 0.6)
    assert raw == pytest.approx(0.75)
    assert s == pytest.approx(0.5)  # (0.75 - 0.5) / (1 - 0.5)
    assert [w["text"] for w in weak] == ["B"]


def test_semantic_score_clamps_below_baseline():
    s, raw, _ = semantic_score(
        job(requirements=[("required", "A")]), np.array([[0.0, 1.0]]), resume(bullets=[[1.0, 0.0]]), 0.5, 0.6
    )
    assert raw == pytest.approx(0.0) and s == 0.0


def test_nice_to_have_requirements_are_never_weak():
    _, _, weak = semantic_score(
        job(requirements=[("nice", "Bonus")]), np.array([[0.0, 1.0]]), resume(bullets=[[1.0, 0.0]]), 0.5, 0.6
    )
    assert weak == []


def test_semantic_falls_back_to_resume_vector_without_bullets():
    s, raw, _ = semantic_score(
        job(requirements=[("required", "A")]), np.array([[1.0, 0.0]]), resume(vector=[1.0, 0.0]), 0.5, 0.6
    )
    assert raw == pytest.approx(1.0) and s == pytest.approx(1.0)


def test_semantic_null_without_requirements():
    assert semantic_score(job(), np.empty((0, 2)), resume(bullets=[[1.0, 0.0]]), 0.5, 0.6)[0] is None


WEIGHTS = {"skill": 0.5, "semantic": 0.35, "structure": 0.15}


def test_final_score_formula():
    assert combine({"skill": 0.8, "semantic": 0.4, "structure": 1.0}, WEIGHTS) == pytest.approx(
        100 * (0.5 * 0.8 + 0.35 * 0.4 + 0.15 * 1.0)
    )


def test_null_skill_weight_moves_to_semantic_not_structure():
    # An unrelated job with no recognizable skills must not score high on formatting alone.
    unrelated = combine({"skill": None, "semantic": 0.0, "structure": 1.0}, WEIGHTS)
    assert unrelated == pytest.approx(15.0)
    assert combine({"skill": None, "semantic": 1.0, "structure": 1.0}, WEIGHTS) == pytest.approx(100.0)


def test_other_null_weights_spread_proportionally():
    assert combine({"skill": 1.0, "semantic": None, "structure": 0.0}, WEIGHTS) == pytest.approx(
        100 * 0.5 / 0.65
    )


def test_tfidf_rewards_shared_vocabulary():
    close = tfidf_score("python django postgres apis", "python django apis postgres developer")
    far = tfidf_score("python django postgres apis", "registered nurse patient care")
    assert close > far >= 0.0
    assert tfidf_score("the and of", "a the") == 0.0  # stop words only: no crash


def test_score_end_to_end_with_weights_from_settings():
    j = job(skills=[("Python", 3.0)], requirements=[("required", "A")], text="python backend")
    r = resume(skills={"Python"}, bullets=[[1.0, 0.0]], structure=0.8, text="python backend work")
    result = score(j, np.array([[1.0, 0.0]]), r, SETTINGS)
    assert (result.skill_score, result.semantic_score, result.structure_score) == (1.0, 1.0, 0.8)
    assert result.final_score == pytest.approx(97.0)
    assert result.tfidf_score > 0
