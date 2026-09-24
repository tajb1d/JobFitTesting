# JobFit

A resume analyzer and job recommender. Upload a resume, get it scored against a real job
posting with specific suggestions, and browse a ranked feed of live openings pulled daily from
company job boards.

Built on free tiers end to end: FastAPI on Render, React on Vercel, Postgres with pgvector on
Supabase, and a GitHub Actions cron for ingestion.

---

## What it does

1. **Reads your resume.** A PDF goes in; out comes the text, its sections, the individual
   bullet points, the skills it names, and a structure score with feedback ("only 1 of 15
   bullets has a number in it").
2. **Scores it against a job.** Paste a description or a Greenhouse/Lever link and get a match
   score, three subscores, the skills you have and lack, requirements your resume doesn't
   cover, and suggestions from Claude.
3. **Recommends live jobs.** A nightly job pulls ~3,000 openings from 60 company boards. The
   feed ranks them against your resume and hides roles far above your level.
4. **Tracks applications.** Save a job, move it through saved → applied → interviewing → offer
   → rejected, and keep notes.

Two things it deliberately doesn't do: it never rewrites your resume or invents experience
(suggestions are phrased as "if you've done this, add it"), and it never submits an
application. "Apply" opens the company's own posting.

---

## Tech stack

### Backend (`backend/`)

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.11 | spaCy and PyMuPDF are the reason this isn't Node |
| Web framework | FastAPI 0.141 | Typed request/response models, automatic OpenAPI docs at `/docs` |
| Server | uvicorn 0.53, **one worker** | The free Render instance has 512 MB; each worker loads its own spaCy model |
| ORM | SQLAlchemy 2.0.54 | Typed `Mapped[]` models, no legacy Query API |
| Migrations | Alembic 1.20 | Schema changes only ever happen through a migration |
| DB driver | psycopg 3.3 | Works with Supabase's session pooler |
| Vectors | pgvector 0.4.2 (server 0.8.2) | HNSW index for nearest-neighbour search inside Postgres |
| Validation | Pydantic 2.13 + pydantic-settings | Every endpoint has a schema; config comes from env |
| Auth | PyJWT 2.14 | Verifies Supabase tokens against its JWKS |
| PDF | PyMuPDF 1.28 | Text plus coordinates and font sizes, which is how headings are detected |
| NLP | spaCy 3.8 + `en_core_web_sm` | Tokenizer and PhraseMatcher only; **not** its NER |
| Keyword baseline | scikit-learn 1.9 | TF-IDF, stored for evaluation, excluded from the score |
| HTTP | httpx 0.28 | One client for Voyage, the ATS APIs, and Supabase admin |
| Embeddings | Voyage `voyage-4-lite`, 512 dims | Cheap, small vectors; the free allowance covers the whole corpus |
| LLM | Anthropic `claude-haiku-4-5-20251001` | Structured JSON feedback, with a template fallback |
| Tests | pytest 8.4, 406 of them | Database tests run against real Supabase inside a rolled-back transaction |

### Frontend (`frontend/`)

| Piece | Choice |
|---|---|
| Build | Vite 8 |
| UI | React 19 + TypeScript 6 |
| Styling | Tailwind 4 (via `@tailwindcss/vite`) |
| Routing | React Router 7 |
| Auth | supabase-js 2 (email + password) |
| Linting | oxlint |

The production bundle is ~149 KB gzipped, most of it supabase-js and the router.

### Infrastructure

| Piece | Choice |
|---|---|
| Database + auth | Supabase (Postgres 17, pgvector, Supabase Auth) |
| API hosting | Render, free Docker web service (image 1.15 GB, runs in ~230 MB) |
| Frontend hosting | Vercel |
| Scheduled work | GitHub Actions: CI on every push, ingestion daily at 09:30 UTC |

---

## How it works

### The resume pipeline (on upload)

```
PDF → PyMuPDF text + layout → headings and sections → bullets
    → PhraseMatcher over skills.json → structure checks → Voyage embeddings → Postgres
```

- **Headings** are found from font size, boldness and line length, checked against a list of
  known section names, so "EXPERIENCE" and "Work Experience" both land correctly.
- **Skills** come from `app/data/skills.json` (319 entries with aliases), never from spaCy's
  NER, which doesn't know that "React" is a framework. Ambiguous one-word skills ("Go", "R")
  only count with supporting context nearby.
- **Structure score** is a checklist worth 100 points: contact details, the expected sections,
  70% of bullets starting with an action verb, at least 3 quantified results, 1–2 pages, and no
  text trapped in tables. Every failed check becomes a feedback item.
- **Embeddings**: one vector for the whole resume (prefixed with your target roles, since that's
  what job search is run against) and one per bullet.

The PDF itself is never stored. The extracted text is, and deleting a resume or your account
removes it.

### Scoring a job

```
final = 100 × (0.50 × skill + 0.35 × semantic + 0.15 × structure)
```

- **Skill match (50%)**: the posting's skills weighted 3.0 if required, 0.5 if only
  "nice to have", 2.0 if mentioned repeatedly, 1.0 otherwise. Your score is the weight you
  cover over the total. If the posting names no recognized skills, this is null and its weight
  goes to the semantic score.
- **Semantic match (35%)**: every requirement bullet is compared against every resume bullet;
  each requirement takes its best match, and those are averaged. The raw average is rescaled
  above a calibrated baseline (`sem_baseline`, currently 0.33, measured by
  `scripts/calibrate.py` over resumes and unrelated jobs). Requirements that match nothing
  become "your resume doesn't cover this" items.
- **Structure (15%)**: the resume's own score, from above.
- **TF-IDF** is computed and stored but deliberately left out of the score. It's the baseline
  the evaluation compares against.

Feedback comes from Claude, which sees only the *structured result*: matched and missing
skills, weak requirements, failed checks, subscores. It never sees your resume text. If the
API fails or returns invalid JSON, templates built from the same data take over, so an analysis
never fails because the LLM did.

### Job ingestion (nightly)

`python -m app.ingestion.run` — no scraping, just the public JSON APIs that Greenhouse and
Lever publish for every company board.

1. **Fetch** all 60 boards in `app/data/companies.json` (~16,000 postings), one request at a
   time with a 1-second gap. Three consecutive failures deactivate a company.
2. **Normalize** to a common shape and convert the description HTML to text.
3. **Diff** on `sha256(title + text)`. Unchanged postings just get their "last seen" bumped,
   which makes a re-run nearly free.
4. **Select** before spending anything on embeddings: most recently posted, at most 150 per
   company, 3,000 overall.
5. **Parse** with the same parser the analyze page uses, then **embed** the job and each
   requirement.
6. **Upkeep**: postings that disappeared are closed; closed jobs are deleted after 14 days
   unless a user saved them; the corpus cap closes the oldest.

It also reports noun phrases that keep appearing in requirements but aren't in `skills.json`,
ranked by how many different companies use them, so the taxonomy can grow. Run it on its own
with `python -m app.ingestion.vocab`.

### The recommendation feed

**Retrieve**: pgvector finds the 100 jobs nearest your resume vector, with remote, location and
level applied as SQL filters in the same query.

**Rerank**: those 100 are scored with the function above, entirely in memory. Requirement
similarities are computed inside Postgres, which avoids shipping ~10 MB of vectors per request
and took a warm response from ~1.8 s to ~1.0 s.

**Eligibility** (from your profile's level): jobs two or more levels above you are hidden
unless "show stretch roles" is on, and each level above multiplies the score by 0.85.

No external API calls happen in either stage.

---

## Data model

Eight tables, all with RLS enabled and no policies: the backend connects as a privileged role,
and this blocks direct access through Supabase's auto-generated REST API.

| Table | Holds |
|---|---|
| `profiles` | Target roles, location, remote preference, experience level |
| `resumes` | Extracted text, sections, skills, structure score and checks, embedding, active flag |
| `resume_bullets` | One row per bullet, with its embedding |
| `companies` | Name, ATS, board token, active flag, consecutive failures |
| `jobs` | Title, location, remote, URL, description, content hash, skills, level, min years, embedding, status |
| `job_requirements` | One row per requirement bullet, with its embedding |
| `analyses` | Scores, subscores, matched/missing skills, weak requirements, feedback |
| `applications` | The tracker: status and notes, unique per user and job |

Every query that touches user data filters by the `user_id` from the auth dependency. RLS does
not protect against our own bugs, because the backend bypasses it.

---

## API

All under `/api/v1`, all requiring a bearer token except `/health`. Interactive docs at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, no auth, no database |
| GET | `/api/v1/me` | Current user and profile |
| PUT | `/api/v1/profile` | Save targets; re-embeds the resume if roles changed |
| POST | `/api/v1/resumes` | Upload a PDF (5 MB max, 10 per user) |
| GET | `/api/v1/resumes` | List |
| GET / PATCH / DELETE | `/api/v1/resumes/{id}` | One resume; PATCH switches the active one |
| POST | `/api/v1/analyses` | Analyze `job_description`, `job_id`, or `job_url` |
| GET | `/api/v1/analyses` | History |
| GET / DELETE | `/api/v1/analyses/{id}` | One analysis |
| GET | `/api/v1/recommendations` | Ranked feed with filters |
| GET | `/api/v1/jobs/{id}` | Job detail |
| GET / POST | `/api/v1/applications` | Tracker |
| PATCH / DELETE | `/api/v1/applications/{id}` | Update status/notes, remove |
| DELETE | `/api/v1/account` | Delete all user data, then the Supabase auth user |

**Auth**: the frontend signs in with supabase-js and sends the access token. The backend
verifies its signature against Supabase's JWKS (ES256/RS256 only), checks the audience, and
uses the `sub` claim as the user id.

**Limits**: 20 analyses per user per hour, since each calls Claude; PDFs up to 5 MB; pasted
descriptions 100–20,000 characters; `job_url` accepts Greenhouse and Lever links only.

---

## Repository layout

```
backend/
  app/
    api/          # Thin routes: parse, call a service, return a schema
    services/     # All the logic, as plain testable functions
      pdf_analyzer.py       structure_analyzer.py   nlp_analyzer.py
      resumes.py            jd_parser.py            eligibility.py
      matcher.py            feedback.py             analyses.py
      recommendations.py    jobs.py                 applications.py
      embeddings.py         job_urls.py             account.py
    ingestion/    # sources.py, pipeline.py, vocab.py, seed.py, run.py
    schemas/      # Pydantic request/response models
    data/         # skills.json (319), companies.json (60), roles.json, action_verbs.txt
    models.py     config.py   db.py   main.py
  alembic/        # Migrations
  scripts/        # get_token.py, calibrate.py
  tests/          # 21 files, 406 tests
frontend/
  src/
    pages/        # Login, Signup, Onboarding, Dashboard, Analyze, Analysis,
                  # Jobs, JobDetail, Tracker, History, Settings
    components/   # Layout, UI primitives, job badges, resume upload
    lib/          # api.ts (the only fetch wrapper), useApi.ts, supabase.ts
    auth/         # Session and resume context
    types/api.ts  # Mirrors the backend's response models
docs/             # plan.md (the design) and build-guide.md (how it was built)
DEPLOY.md         # Deployment walkthrough
render.yaml       # Render Blueprint
```

Conventions worth knowing before contributing:

- Routes stay thin; logic lives in services, which are plain functions with no FastAPI imports.
- Every request and response body has a Pydantic schema. No raw dicts leave an endpoint.
- Voyage is only ever called from `services/embeddings.py`, Anthropic only from
  `services/feedback.py`. Tests mock both.
- Schema changes happen through Alembic. Never by hand.
- Tunables (scoring weights, caps, rate limits) live in `app/config.py`, not scattered in code.

---

## Running it locally

**Prerequisites**: Python 3.11, Node 20+, and a Supabase project with the `vector` extension.

### Backend

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill it in
alembic upgrade head
uvicorn app.main:app --reload # http://localhost:8000/docs
```

`backend/.env`:

```
DATABASE_URL=        # Supabase session pooler (IPv4), postgresql+psycopg://…
SUPABASE_URL=
SUPABASE_SECRET_KEY= # server only: bypasses RLS, deletes auth users
VOYAGE_API_KEY=
ANTHROPIC_API_KEY=   # must be scoped to a workspace
CORS_ORIGINS=http://localhost:5173
```

Use the **session pooler** connection string. The direct one isn't reachable from Render or
GitHub Actions.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env          # VITE_SUPABASE_PUBLISHABLE_KEY is the sb_publishable_… key
npm run dev                   # http://localhost:5173
```

### Fill the job corpus

```bash
cd backend
python -m app.ingestion.run --limit-companies 3   # ~30 jobs, a few minutes
python -m app.ingestion.run                       # all 60, ~3,000 jobs, ~15 minutes
```

Voyage throttles accounts without a payment method to 3 requests/minute, which turns a full run
into hours. With billing enabled the defaults in `config.py` apply. The tokens themselves fall
inside the free allowance.

---

## Testing

```bash
cd backend && pytest            # 406 tests
```

- **Offline tests** (319) need nothing: PDF parsing, structure scoring, skill matching, job
  parsing, eligibility, the matcher, rate limiting, URL parsing, and the API with fakes.
- **Database tests** (87) run against real Supabase inside a transaction that is always rolled
  back, so nothing persists. Without `DATABASE_URL` they skip themselves, which is how CI runs
  with no secrets.
- Voyage and Anthropic are always faked. Tests never spend money.

Frontend: `npm run lint`, `npx tsc --noEmit`, `npm run build`.

---

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for the dashboard-by-dashboard walkthrough. In short: Render
reads `render.yaml` and builds `backend/Dockerfile`; Vercel builds `frontend/`; GitHub Actions
needs `DATABASE_URL` and `VOYAGE_API_KEY` for the ingestion cron.

---

## Known limits

- **60 companies**, all tech, all on Greenhouse or Lever. Anything else has to be pasted into
  Analyze by hand.
- **3,000 jobs**, capped to stay inside Supabase's free 500 MB, keeping the most recent.
- **Up to a day stale**: a job filled this morning stays in the feed until tonight's run.
- **Seniority comes from the title**, so a "Manager" title that isn't people management can
  still be misread. Titles with no level word get no level at all.
- **`min_years` takes the smallest number** in the requirements, which understates roles asking
  for "5 years engineering, 2 as a lead". The feed only uses it to hide jobs, so erring low
  shows more rather than fewer.
- **The semantic subscore is compressed.** It ranks related jobs above unrelated ones reliably
  (AUC ~0.85 in a title-labelled check), but raw similarities sit in a narrow 0.25–0.50 band,
  so after rescaling it moves the final score by only a few points.
- **Render's free instance sleeps** after 15 minutes, so the first request can take a minute.
  The UI says so while it waits.

---

## Costs

Free: Render's web service, Vercel hobby, Supabase's free project, GitHub Actions, and Voyage's
free token allowance (the full 3,000-job corpus is ~5.2M tokens). Anthropic is pay-as-you-go,
but an analysis is a fraction of a cent and the 20-per-hour cap bounds it.

---

## Design documents

- **[docs/plan.md](docs/plan.md)** — the full design: architecture, scoring formulas, schema,
  API, and the eligibility rules.
- **[docs/build-guide.md](docs/build-guide.md)** — the session-by-session build order.
- **[CLAUDE.md](CLAUDE.md)** — conventions and gotchas for working in this repo.
