"""Compute SEM_BASELINE (plan §6): the mean S_sem_raw of test resumes against ~50 random
unrelated corpus jobs each. Prints the value to put in app/config.py (sem_baseline).

Usage (from backend/):
    python scripts/calibrate.py [--jobs 50] [--seed 7] [resume.pdf ...]

Defaults to the PDFs in tests/fixtures/. Resume bullets are embedded with Voyage (one call
per resume); job requirement vectors come from the database, so run ingestion first.

"Unrelated" = the job and the resume are in different categories (technical vs
non-technical, by whether they name any language/framework/database/cloud/devops skill) and
share no skills at all."""

import argparse
import random
import statistics
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.models import Job, JobRequirement  # noqa: E402
from app.services.embeddings import VoyageProvider  # noqa: E402
from app.services.jd_parser import ParsedJob, Requirement  # noqa: E402
from app.services.matcher import ResumeForScoring, semantic_score  # noqa: E402
from app.services.resumes import analyze_resume  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
TECH_CATEGORIES = {"language", "framework", "database", "cloud", "devops"}


def is_technical(skills: list[dict]) -> bool:
    return any(s.get("category") in TECH_CATEGORIES for s in skills)


def raw_score(reqs: list[tuple], resume: ResumeForScoring) -> float:
    parsed = ParsedJob("", "", [], [Requirement(s, t) for s, t, _ in reqs], [], None, None)
    _, raw, _ = semantic_score(parsed, np.array([v for _, _, v in reqs]), resume, 0.0, 0.0)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--jobs", type=int, default=50, help="unrelated jobs per resume")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    pdfs = args.pdfs or sorted(FIXTURES.glob("*.pdf"))
    if not pdfs:
        print("No resume PDFs given and none in tests/fixtures/.", file=sys.stderr)
        return 1

    settings = get_settings()
    embedder = VoyageProvider(settings.voyage_api_key)
    with get_sessionmaker()() as db:
        jobs = db.execute(select(Job.id, Job.title, Job.skills).where(Job.status == "open")).all()
        requirements: dict = {}
        for job_id, section, text, vector in db.execute(
            select(JobRequirement.job_id, JobRequirement.section, JobRequirement.text,
                   JobRequirement.embedding).order_by(JobRequirement.id)
        ):
            requirements.setdefault(job_id, []).append((section, text, vector))

    all_raw: list[float] = []
    all_related: list[float] = []
    for pdf in pdfs:
        analysis = analyze_resume(pdf.read_bytes())
        resume_skills = [{"skill": s.canonical, "category": s.category} for s in analysis.skills]
        names = {s["skill"] for s in resume_skills}
        technical = is_technical(resume_skills)
        bullets = [b.text for b in analysis.structure.bullets]
        resume = ResumeForScoring(
            text=analysis.text,
            skills=names,
            bullet_vectors=np.array(embedder.embed(bullets, "document")) if bullets else np.empty((0, 0)),
            resume_vector=np.array(embedder.embed([analysis.text], "query")[0]),
            structure_score=analysis.structure.score,
        )

        pool = [j for j in jobs
                if j.id in requirements
                and is_technical(j.skills or []) != technical
                and not names & {s["skill"] for s in (j.skills or [])}]
        raws = [raw_score(requirements[j.id], resume)
                for j in random.Random(args.seed).sample(pool, min(args.jobs, len(pool)))]
        all_raw += raws
        # Sanity check, not part of the baseline: related jobs (same category, 2+ shared
        # skills) should score clearly above it.
        related = [j for j in jobs
                   if j.id in requirements and is_technical(j.skills or []) == technical
                   and len(names & {s["skill"] for s in (j.skills or [])}) >= 2]
        related_raws = [raw_score(requirements[j.id], resume)
                        for j in random.Random(args.seed).sample(related, min(args.jobs, len(related)))]
        all_related += related_raws
        kind = "technical" if technical else "non-technical"
        line = (f"{pdf.name}: {kind} resume, {len(bullets)} bullets, {len(pool)} unrelated jobs, "
                f"sampled {len(raws)}: mean S_sem_raw {statistics.mean(raws):.3f} "
                f"(min {min(raws):.3f}, max {max(raws):.3f})")
        if related_raws:
            line += f"; related jobs {statistics.mean(related_raws):.3f}"
        print(line)

    baseline = statistics.mean(all_raw)
    print(f"\nSEM_BASELINE = {baseline:.2f}  (mean of {len(all_raw)} resume/job pairs, "
          f"stdev {statistics.stdev(all_raw):.3f}; currently {settings.sem_baseline})")
    if all_related:
        print(f"Related jobs average S_sem_raw {statistics.mean(all_related):.3f} "
              f"(S_sem {(statistics.mean(all_related) - baseline) / (1 - baseline):.2f} at this baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
