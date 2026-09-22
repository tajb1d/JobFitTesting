"""Resume ↔ job scoring (plan §6): S_skill, S_sem, S_struct, final, and the TF-IDF baseline.

Pure functions over already-computed inputs (parsed job, embeddings, stored resume data),
so they're unit-testable without a server, database, or network."""

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import Settings
from app.services.jd_parser import ParsedJob

MAX_WEAK_REQUIREMENTS = 5
# Nice-to-have requirements never count as "weakly covered": missing them isn't a gap.
_WEAK_CANDIDATE_SECTIONS = {"required", "responsibilities"}


@dataclass
class ResumeForScoring:
    text: str
    skills: set[str]                 # canonical names
    bullet_vectors: np.ndarray       # (n_bullets, dim); may be empty
    resume_vector: np.ndarray | None  # fallback when the resume has no bullets
    structure_score: float           # S_struct, stored at upload


@dataclass
class MatchResult:
    final_score: float
    skill_score: float | None
    semantic_score: float | None
    structure_score: float
    tfidf_score: float
    semantic_raw: float | None
    matched_skills: list[dict]
    missing_skills: list[dict]
    weak_requirements: list[dict]


def skill_score(job: ParsedJob, resume_skills: set[str]) -> tuple[float | None, list, list]:
    """S_skill = weight of JD skills present in the resume / weight of all JD skills.
    None when the JD has no recognized skills."""
    matched = [{"skill": s.canonical, "weight": s.weight} for s in job.skills
               if s.canonical in resume_skills]
    missing = [{"skill": s.canonical, "weight": s.weight} for s in job.skills
               if s.canonical not in resume_skills]
    total = sum(s.weight for s in job.skills)
    if total == 0:
        return None, matched, missing
    return sum(m["weight"] for m in matched) / total, matched, missing


def _unit(rows: np.ndarray) -> np.ndarray:
    rows = np.atleast_2d(np.asarray(rows, dtype=float))
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    return rows / np.where(norms == 0, 1, norms)


def semantic_score(
    job: ParsedJob,
    requirement_vectors: np.ndarray,
    resume: ResumeForScoring,
    baseline: float,
    weak_threshold: float,
) -> tuple[float | None, float | None, list[dict]]:
    """S_sem: for each requirement, its best cosine match among resume bullets; S_sem_raw is
    the mean; S_sem rescales it above SEM_BASELINE to [0, 1]. Returns
    (S_sem, S_sem_raw, weakly covered requirements)."""
    if not job.requirements or len(requirement_vectors) == 0:
        return None, None, []
    targets = resume.bullet_vectors
    if len(targets) == 0:
        if resume.resume_vector is None:
            return None, None, []
        targets = np.atleast_2d(resume.resume_vector)

    sims = _unit(requirement_vectors) @ _unit(targets).T
    best = sims.max(axis=1)
    raw = float(best.mean())
    scaled = min(max((raw - baseline) / (1 - baseline), 0.0), 1.0)

    weak = sorted(
        (
            {"text": r.text, "section": r.section, "similarity": round(float(b), 3)}
            for r, b in zip(job.requirements, best, strict=True)
            if r.section in _WEAK_CANDIDATE_SECTIONS and b < weak_threshold
        ),
        key=lambda w: w["similarity"],
    )[:MAX_WEAK_REQUIREMENTS]
    return scaled, raw, weak


def tfidf_score(resume_text: str, job_text: str) -> float:
    """Keyword-overlap baseline (plan §6). Stored for evaluation, never used in `final`."""
    try:
        matrix = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), sublinear_tf=True
        ).fit_transform([resume_text, job_text])
    except ValueError:  # empty vocabulary (e.g. text made only of stop words)
        return 0.0
    return float(cosine_similarity(matrix[0], matrix[1])[0, 0])


def combine(subscores: dict[str, float | None], weights: dict[str, float]) -> float:
    """final = 100 × weighted sum of subscores, with null weights redistributed (plan §6).

    A null S_skill (no recognized skills in the JD) hands its weight to S_sem, the other
    job-relevance signal. Spreading it proportionally would also inflate S_struct, which
    describes only the resume, so an unrelated job would score ~30 on formatting alone.
    Any other null weight is spread proportionally over what's left."""
    weights = dict(weights)
    if subscores.get("skill") is None and subscores.get("semantic") is not None:
        weights["semantic"] += weights["skill"]
        weights["skill"] = 0.0
    present = {k: v for k, v in subscores.items() if v is not None}
    total_weight = sum(weights[k] for k in present)
    if total_weight == 0:
        return 0.0
    return 100 * sum(weights[k] * v for k, v in present.items()) / total_weight


def score(
    job: ParsedJob,
    requirement_vectors: np.ndarray,
    resume: ResumeForScoring,
    settings: Settings,
) -> MatchResult:
    s_skill, matched, missing = skill_score(job, resume.skills)
    s_sem, raw, weak = semantic_score(
        job,
        requirement_vectors,
        resume,
        settings.sem_baseline,
        settings.sem_baseline + settings.weak_requirement_margin,
    )
    s_struct = resume.structure_score
    final = combine(
        {"skill": s_skill, "semantic": s_sem, "structure": s_struct},
        {
            "skill": settings.weight_skill,
            "semantic": settings.weight_semantic,
            "structure": settings.weight_structure,
        },
    )
    return MatchResult(
        final_score=round(final, 1),
        skill_score=None if s_skill is None else round(s_skill, 4),
        semantic_score=None if s_sem is None else round(s_sem, 4),
        structure_score=round(s_struct, 4),
        tfidf_score=round(tfidf_score(resume.text, job.text), 4),
        semantic_raw=None if raw is None else round(raw, 4),
        matched_skills=matched,
        missing_skills=missing,
        weak_requirements=weak,
    )
