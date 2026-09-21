# JobFit — Technical Plan

This is the source of truth. If code and this document disagree, fix one of them
deliberately and note why.

---

## 1. What JobFit does

1. A user registers and logs in. Nothing is usable without an account.
2. They upload a resume PDF and set targets: 1–3 role titles (pre-filled from the resume),
   location / remote preference, experience level.
3. They immediately get a **general review** of the resume — structure and hygiene,
   no job needed.
4. Two ways to get a **job analysis**, which is the same thing either way:
   - paste a job description (or a supported Greenhouse/Lever link), or
   - click "Full analysis" on a job in their **Jobs for you** feed.
5. The feed ranks real, currently open postings by fit to their resume and targets,
   with a few filters. It is not a browsable job board.
6. They save jobs, open the real application page, and track status.

**Out of scope:** auto-submitting applications, scraping LinkedIn/Indeed, rewriting resumes.

---

## 2. Architecture

```
React (Vercel) ──JSON/HTTPS──▶ FastAPI (Render)
      │                           │
      │ login only                ├─ services/ (pdf, structure, nlp, matcher,
      ▼                           │            eligibility, retrieval, feedback)
Supabase Auth                     ├──▶ Voyage API     (embeddings)
      ▲                           ├──▶ Anthropic API  (feedback text)
      │ JWKS public keys          ▼
      └──────────────────── Supabase Postgres + pgvector
                                  ▲
GitHub Actions (daily cron) ──────┘  ingestion: Greenhouse + Lever public boards
```

- The frontend talks to Supabase **only for login**. All data goes through FastAPI.
- FastAPI verifies the Supabase access token itself using the project's public JWKS.
- Ingestion is a separate Python entry point in the same codebase, run by GitHub Actions.

---

## 3. Stack

| Layer | Choice |
|---|---|
| Frontend | React + Vite + TypeScript, Tailwind CSS, React Router, `@supabase/supabase-js` |
| Backend | Python 3.11, FastAPI, Uvicorn (1 worker), Pydantic v2, pydantic-settings |
| DB access | SQLAlchemy 2.0, Alembic, psycopg 3, `pgvector` Python package |
| Auth | Supabase Auth; backend verifies JWTs with PyJWT `PyJWKClient` against JWKS |
| PDF | PyMuPDF |
| NLP | spaCy `en_core_web_sm` + `PhraseMatcher` over `app/data/skills.json` |
| Keyword baseline | scikit-learn TF-IDF |
| Embeddings | Voyage `voyage-4-lite` (512 dims if supported; the dimension is one config constant) |
| Feedback | Anthropic `claude-haiku-4-5-20251001` |
| HTTP client | httpx |
| Hosting | Render (backend), Vercel (frontend), Supabase (DB+auth), GitHub Actions (CI + cron) |

`EMBEDDING_DIM` lives in config. The pgvector columns use it. If you change models, you
change one constant and write one migration.

---

## 4. Auth

Frontend signs users in with supabase-js and sends `Authorization: Bearer <access_token>`.

Backend dependency `get_current_user`:
1. Read the bearer token. Missing → 401.
2. Fetch and cache the JWKS from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`
   (`PyJWKClient`, cached; it refetches when it sees an unknown `kid`).
3. Verify signature (accept ES256/RS256 only), `exp`, and `aud == "authenticated"`.
4. Return the `sub` claim as the user's UUID.

Every route touching user data takes `user_id = Depends(get_current_user)` and filters on it.

---

## 5. The resume pipeline (runs on upload)

1. **Extract** (`pdf_analyzer.py`): PyMuPDF text + text blocks with coordinates and font
   sizes. If extracted text < ~200 chars, reject with "looks like a scanned or image-only PDF."
2. **Sections** (`structure_analyzer.py`): detect headings (short lines, larger/bold font, or
   matching a heading list: Experience, Education, Skills, Projects, Summary, Certifications…).
3. **Bullets**: split experience and project sections into individual bullets.
4. **Skills** (`nlp_analyzer.py`): PhraseMatcher (`attr="LOWER"`) against the taxonomy.
5. **Embeddings**: one Voyage call. The whole resume (`input_type="query"`, prefixed with the
   user's target roles) → `resumes.embedding`. Each bullet (`input_type="document"`) →
   `resume_bullets.embedding`.
6. **Structure score** and **general feedback** (checklist below).
7. **Suggested target roles**: inferred from the resume for onboarding pre-fill.

### Skill taxonomy — `app/data/skills.json`

```json
{"canonical": "PostgreSQL", "category": "database", "aliases": ["postgres", "postgresql", "psql"]}
```

Categories: language, framework, database, cloud, devops, tool, data, concept, soft.
Start with ~300 entries. Ingestion logs frequent unmatched noun chunks so the list can grow.

### Structure checklist → S_struct

| Check | Points |
|---|---|
| Email and phone present | 10 |
| Experience section present | 15 |
| Education section present | 10 |
| Skills section present | 10 |
| ≥70% of bullets start with an action verb (`app/data/action_verbs.txt`) | 20 |
| ≥3 bullets contain a quantified result (number, %, $) | 20 |
| 1–2 pages | 10 |
| No significant text trapped in tables/images | 5 |

`S_struct = points / 100`. Each failed check produces a general-feedback item.

---

## 6. Job analysis — the scoring system

The same function scores a pasted job description and a corpus job. Pasted descriptions are
parsed on the fly; corpus jobs were parsed at ingestion.

### Parsing a job description
- Convert HTML to text. Split into sections by headings (Requirements, Qualifications,
  Responsibilities, Nice to have, …).
- Split into requirement bullets (lines/sentences in requirement-type sections, or all
  bullet-like lines if no sections are found).
- Extract skills with the same PhraseMatcher.
- Extract experience requirements (§8).

### S_skill — weighted skill coverage (weight 0.50)

Weight each JD skill: 3.0 if it appears in a required/qualifications section, 2.0 if it
appears 2+ times anywhere, 1.0 otherwise; 0.5 if it appears only under "nice to have."

```
S_skill = sum(weights of JD skills present in resume) / sum(weights of all JD skills)
```

If the JD has no recognized skills, S_skill = null and its weight is redistributed.

### S_sem — requirement-level semantic match (weight 0.35)

```
For each requirement bullet r_i:
    best(r_i) = max cosine(r_i, e_j) over all resume bullets e_j
S_sem_raw = mean(best(r_i))
S_sem = clamp((S_sem_raw - SEM_BASELINE) / (1 - SEM_BASELINE), 0, 1)
```

`SEM_BASELINE` is in config. `scripts/calibrate.py` computes it: the mean S_sem_raw of
several test resumes against ~50 random unrelated corpus jobs. Until that has been run, use
0.5. Requirements whose `best` is low become "weakly covered requirement" feedback items.

### S_struct (weight 0.15)
From §5.

### Final

```
final = 100 * (0.50 * S_skill + 0.35 * S_sem + 0.15 * S_struct)
```

Weights are config values. Every response returns all three subscores and the final score.

### TF-IDF
Computed and stored as `tfidf_score` for every analysis but **not** used in `final`. It is
the baseline the evaluation compares against.

### Feedback (`feedback.py`)
Only runs for a full analysis, never while building the feed. Sends Claude the *structured
result* (matched/missing skills with weights, weak requirements, failed structure checks,
subscores) — not the raw resume. Asks for JSON:

```json
[{"type": "missing_skill|weak_requirement|structure|strength",
  "severity": "high|medium|low",
  "message": "..."}]
```

System prompt rules: suggest additions the user may genuinely have ("If you've used Docker,
add it and name the project"); never invent experience; never rewrite the resume; 5–8 items;
at least one strength. On API failure or invalid JSON, fall back to template messages built
from the same structured result, so an analysis never fails because of the LLM.

---

## 7. Job ingestion (daily, GitHub Actions)

Entry point: `python -m app.ingestion.run [--limit-companies N]`

1. Load active rows from `companies` (seeded from `app/data/companies.json`, ~60 tech
   companies known to use Greenhouse or Lever).
2. Fetch:
   - Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
   - Lever: `GET https://api.lever.co/v0/postings/{token}?mode=json`
   404 or error → log, mark the company inactive after 3 consecutive failures, continue.
   Be polite: sequential requests, a small delay, a descriptive User-Agent.
3. Normalize to: external_id, title, location, is_remote, url, description_html → text.
4. `content_hash = sha256(title + description_text)`. Unchanged hash → just bump `last_seen`.
5. New or changed: parse (§6), extract skills and eligibility (§8), embed the job
   (`title + description`, `input_type="document"`) and each requirement bullet, upsert
   `jobs` and replace its `job_requirements`.
6. Jobs not seen in this run → `status = 'closed'`. Delete closed jobs older than 14 days.
7. Cap the corpus at ~3,000 open jobs (keep the most recently posted) to stay well inside
   Supabase's free 500 MB.
8. Print a summary: companies ok/failed, jobs new/updated/unchanged/closed, embedding calls.

Batch embedding calls (many texts per request).

---

## 8. Eligibility

From each job:
- **Level from title**: intern; entry (new grad, junior, associate, "I"); mid; senior
  (senior, sr, "III"); staff+ (staff, principal, lead, architect, manager, director).
- **Years**: regex over the description for patterns like "3+ years", "3-5 years",
  "minimum of 5 years", "at least two years" (handle number words). Store the smallest
  required value found in a requirements context as `min_years`.

User's level comes from their profile. In the feed:
- Default: hide jobs two or more levels above the user, or with `min_years` ≥ user max + 3.
- Otherwise multiply the reranked score by 0.85 per level above the user.
- Toggle "Show stretch roles" disables hiding (penalty still applies).

---

## 9. Recommendations — retrieve, then rerank

`GET /api/v1/recommendations?resume_id=&remote=&location=&level=&show_stretch=&limit=20`

1. **Retrieve**: pgvector cosine search on `jobs.embedding` using `resumes.embedding`,
   `status = 'open'`, SQL filters for remote/location, top 100. HNSW index.
2. **Rerank**: for each candidate, compute S_skill (precomputed job skills), S_sem (requirement
   embeddings from DB vs. resume bullet embeddings, numpy), S_struct (from the resume), final,
   then the eligibility adjustment. No API calls in this step.
3. Return the top `limit` with score, subscores, and top 3 matched + top 3 missing skills.

Target latency: under 2 seconds warm.

---

## 10. Database schema

All tables in `public`. Enable RLS on every table **with no policies**: the backend's privileged
role bypasses RLS, and this blocks direct reads through Supabase's auto-generated REST API with
the publishable key.

```
profiles        user_id UUID PK → auth.users ON DELETE CASCADE
                target_roles TEXT[], location TEXT, remote_ok BOOL, experience_level TEXT,
                updated_at

resumes         id UUID PK, user_id → auth.users CASCADE, filename, text,
                sections JSONB, skills JSONB, structure_score NUMERIC,
                structure_checks JSONB, general_feedback JSONB, suggested_roles TEXT[],
                embedding VECTOR(EMBEDDING_DIM), is_active BOOL, created_at

resume_bullets  id PK, resume_id → resumes CASCADE, section TEXT, text,
                embedding VECTOR(EMBEDDING_DIM)

companies       id PK, name, ats ('greenhouse'|'lever'), board_token, active BOOL,
                consecutive_failures INT, UNIQUE(ats, board_token)

jobs            id UUID PK, company_id → companies, external_id, title, location,
                is_remote BOOL, url, description_text, content_hash, skills JSONB,
                level TEXT, min_years INT, embedding VECTOR(EMBEDDING_DIM),
                status ('open'|'closed'), posted_at, first_seen, last_seen,
                UNIQUE(company_id, external_id)
                HNSW index on embedding (vector_cosine_ops); index on status

job_requirements id PK, job_id → jobs CASCADE, section TEXT, text,
                 embedding VECTOR(EMBEDDING_DIM)

analyses        id UUID PK, user_id → auth.users CASCADE, resume_id → resumes CASCADE,
                job_id → jobs SET NULL (null when pasted), job_title, job_description_text,
                final_score, skill_score, semantic_score, structure_score, tfidf_score,
                matched_skills JSONB, missing_skills JSONB, weak_requirements JSONB,
                feedback_items JSONB, created_at

applications    id UUID PK, user_id → auth.users CASCADE, job_id → jobs CASCADE,
                status ('saved'|'applied'|'interviewing'|'offer'|'rejected'),
                notes TEXT, created_at, updated_at, UNIQUE(user_id, job_id)
```

`CREATE EXTENSION IF NOT EXISTS vector;` goes in the first migration.

Privacy: PDFs are never stored. Extracted resume text is stored and is personal data.
Users can delete a resume, an analysis, or their whole account (cascades).

---

## 11. API

All under `/api/v1`, all require auth except `/health`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness (no auth, no DB) |
| GET | `/me` | Current user id + profile |
| PUT | `/profile` | Set targets |
| POST | `/resumes` | Upload PDF → processed resume + general review |
| GET | `/resumes` | List user's resumes |
| GET / DELETE | `/resumes/{id}` | One resume |
| POST | `/analyses` | Body: `resume_id` + one of `job_description`, `job_id`, `job_url` |
| GET | `/analyses` | History |
| GET / DELETE | `/analyses/{id}` | One analysis |
| GET | `/recommendations` | Ranked feed (§9) |
| GET | `/jobs/{id}` | Job detail |
| GET / POST | `/applications` | List / save a job |
| PATCH / DELETE | `/applications/{id}` | Update status/notes / remove |
| DELETE | `/account` | Delete all user data, then the Supabase auth user (service key) |

`job_url` accepts Greenhouse/Lever posting URLs only; anything else → 422 with a message
asking the user to paste the description.

Limits: PDF ≤ 5 MB, `application/pdf` only; job description 100–20,000 chars. Per-user
rate limit on `POST /analyses` (e.g. 20/hour) because it calls the LLM.

---

## 12. Frontend pages

| Route | Page |
|---|---|
| `/login`, `/signup` | Supabase email + password |
| `/onboarding` | Upload resume, confirm suggested roles, location/remote, level |
| `/dashboard` | Active resume's structure score, general feedback, links to Analyze and Jobs |
| `/analyze` | Paste a description or supported link → creates an analysis |
| `/analyses/:id` | Score, three subscores, matched/missing skills, weak requirements, feedback |
| `/jobs` | Jobs for you: ranked cards, filters, "Full analysis", "Save" |
| `/jobs/:id` | Job detail, "Full analysis", "Apply" (opens real URL in a new tab), "Save" |
| `/tracker` | Saved jobs grouped by status; change status, add notes |
| `/history` | Past analyses |
| `/settings` | Manage resumes, delete account |

Protected routes redirect to `/login`. Users with no resume are sent to `/onboarding`.
Because Render's free tier sleeps, the API client shows a "waking up the server — up to a
minute" state if the first request takes longer than ~3 seconds.

---

## 13. Environment variables

**Backend** (`backend/.env`, Render, GitHub Actions)
```
DATABASE_URL=              # Supabase session pooler string, postgresql+psycopg://...
SUPABASE_URL=
SUPABASE_SECRET_KEY=       # server-only; used for account deletion
VOYAGE_API_KEY=
ANTHROPIC_API_KEY=
CORS_ORIGINS=              # comma-separated, e.g. http://localhost:5173,https://<app>.vercel.app
```

**Frontend** (`frontend/.env`, Vercel)
```
VITE_API_BASE_URL=
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
```

---

## 14. Evaluation

1. **Gold set**: 10 resumes × 10 job descriptions, each pair labeled 1–5 by teammates.
   Report Spearman correlation of `final` vs. labels, `tfidf_score` vs. labels, and an
   ablation dropping each subscore.
2. **Ranking**: 5 test resumes, top 20 recommendations each, labeled relevant / not.
   Report precision@10 for retrieval-only order vs. retrieve-then-rerank.
3. **Eligibility**: hand-label level and min_years on 100 jobs; report extractor accuracy.

`scripts/` holds the runners and writes CSVs for the report.
