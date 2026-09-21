# JobFit — Build Guide

Nine sessions. Each one ends in something you can verify. Don't start the next session
until the current one passes its check.

**How to run a session in VS Code**
1. Open the Claude Code panel and start a fresh conversation (`/clear`). One session per
   conversation keeps the context clean.
2. Switch to **Plan mode** (Shift+Tab until it says plan mode), paste the prompt, and read
   the plan it proposes. Push back if it's substituting parts of the stack.
3. Approve, let it build, let it run the tests.
4. Do the **You verify** step yourself. Then commit.

---

## Session 0 — Setup (you, ~45 minutes, no Claude)

**Install:** Git, Python 3.11+, Node 20+, Docker Desktop (only needed to test the image).

**Repo:** create an empty GitHub repo, clone it, then add:
```
CLAUDE.md
docs/plan.md
docs/build-guide.md
```
Commit and push.

**Supabase** (supabase.com → New project; save the database password somewhere safe)
- Project Settings → API keys: copy the **Project URL**, **publishable key**, and **secret key**.
- Connect → **Session pooler** connection string. Change the prefix to
  `postgresql+psycopg://` and fill in your password.
- Authentication → Sign In / Providers → Email: for this mock, turn **off** "Confirm email"
  (the built-in email sender is heavily rate-limited).
- Authentication → Users → Add user: create a test user with a password. You'll use it to
  get tokens for testing.

**Voyage AI** (voyageai.com): create an API key. The voyage-4 family includes a large
free token allowance, which is far more than this project will use.

**Anthropic Console** (console.anthropic.com): create an API key, add ~$5 of credits,
and set a low monthly spend limit.

**Render** and **Vercel**: sign up with GitHub. Nothing to configure yet.

**Local env file:** create `backend/.env` from the variable list in `docs/plan.md` §13
with `CORS_ORIGINS=http://localhost:5173`. Double-check `.env` is in `.gitignore`
before your next commit.

**Test PDFs:** put 2–3 real resume PDFs in `backend/tests/fixtures/` — yours or anonymized
samples. Add that folder to `.gitignore` if they contain real personal info.

---

## Session 1 — Backend foundation

```
Read CLAUDE.md and docs/plan.md. This is Session 1 from docs/build-guide.md. Do only this.

Build the backend foundation in backend/:
- FastAPI app with the folder layout from CLAUDE.md, app/config.py using pydantic-settings
  reading backend/.env, CORS from CORS_ORIGINS, GET /health.
- requirements.txt with everything the plan's stack needs (include python-multipart,
  psycopg[binary], pgvector, pyjwt[crypto], httpx). Pin major versions.
- SQLAlchemy 2.0 models for every table in plan §10, EMBEDDING_DIM from config.
- Alembic set up, with one initial migration: enable the vector extension, create all tables,
  indexes including the HNSW index, and enable RLS with no policies on every table.
- The get_current_user dependency from plan §4 (JWKS via PyJWKClient, cached).
- GET /api/v1/me and PUT /api/v1/profile.
- scripts/get_token.py: signs in the test user via Supabase's password grant endpoint and
  prints an access token (email/password from env vars TEST_USER_EMAIL / TEST_USER_PASSWORD).
- pytest setup with tests for /health, auth rejection (missing/garbage/expired token), and
  profile read/write using a dependency override for the auth user.
- backend/.env.example with every variable, empty.

Run the tests and the migration against DATABASE_URL. Show me the commands you ran.
```

**You verify:**
- Supabase → Table Editor shows all the tables.
- `uvicorn app.main:app --reload`, open `http://localhost:8000/docs`.
- Run `python scripts/get_token.py`, click **Authorize** in `/docs`, paste the token, and
  call `/api/v1/me`. You get your user id back. A garbage token gets 401.

---

## Session 2 — Resume pipeline

```
Read CLAUDE.md and docs/plan.md. This is Session 2. Do only this.

Implement plan §5 end to end:
- app/data/skills.json with ~300 skills in the documented format (aliases included, tech-heavy
  but with some data, cloud, and soft skills), and app/data/action_verbs.txt.
- services: pdf_analyzer.py, structure_analyzer.py (sections, bullets, the checklist and
  S_struct), nlp_analyzer.py (PhraseMatcher, loaded once at startup), embeddings.py
  (EmbeddingProvider protocol + VoyageProvider using voyage-4-lite at EMBEDDING_DIM, batched,
  with input_type), and suggested role inference.
- POST /api/v1/resumes (PDF only, ≤5 MB, rejects image-only PDFs with a clear message),
  GET /api/v1/resumes, GET and DELETE /api/v1/resumes/{id}. Uploading makes the new resume
  active. Store resume_bullets with embeddings.
- Response includes structure score, each checklist result, general feedback items, skills,
  and suggested roles.
- Unit tests for each service using the PDFs in tests/fixtures (skip gracefully if absent)
  plus small synthetic text cases. Mock Voyage in tests.

Then run the real endpoint once against a fixture PDF with the real Voyage key and show me
the JSON response.
```

**You verify:** upload your resume through `/docs`. Do the detected sections, skills, and
checklist results match what's actually on your resume? Fix the taxonomy or heuristics now
if not — every later step depends on this.

---

## Session 3 — Job analysis and scoring

```
Read CLAUDE.md and docs/plan.md. This is Session 3. Do only this.

Implement plan §6:
- A job-description parser (HTML→text, sections, requirement bullets, skills with weights).
- The eligibility extractor from plan §8 (level from title, min_years regex including number
  words), with thorough unit tests — at least 25 cases.
- matcher.py: S_skill, S_sem (requirement max-sim against resume bullets, calibrated with
  SEM_BASELINE), S_struct reuse, final score, and tfidf_score. Weights from config.
- feedback.py using the Anthropic API with the rules in §6, JSON output, validation, and the
  template fallback on any failure.
- POST /api/v1/analyses with job_description text (job_id and job_url come later — return 501
  for those for now), GET /api/v1/analyses, GET and DELETE /api/v1/analyses/{id}.
- A per-user rate limit on POST /analyses.
- Tests with mocked external APIs.

Then run a real analysis of my fixture resume against a short realistic software engineer job
description you write, and against an unrelated one (e.g. a registered nurse posting). Show me
both results side by side.
```

**You verify:** the software job should clearly outscore the nursing job. If they're close,
the semantic layer or the skill weights are wrong. Read the feedback items: do they suggest
additions rather than invent experience?

---

## Session 4 — Job ingestion

```
Read CLAUDE.md and docs/plan.md. This is Session 4. Do only this.

Implement plan §7:
- app/data/companies.json: ~60 tech companies with their ATS (greenhouse or lever) and board
  token. Prefer companies that hire new grads. Verify each token by requesting its board and
  drop any that 404.
- A seed step that upserts companies from that file.
- app/ingestion/: Greenhouse and Lever fetchers, normalization, hashing, parsing with the
  Session 3 parser, eligibility, batched embeddings, upsert, closing missing jobs, pruning,
  the corpus cap, and the summary printout. Runnable as
  python -m app.ingestion.run [--limit-companies N].
- Tests with recorded sample payloads for both ATS formats (no live network in tests).

Run it with --limit-companies 3 first and show me the summary, then run it for all companies.
```

**You verify:** Supabase shows a few thousand rows in `jobs` and many more in
`job_requirements`. Spot-check 10 jobs: are `level` and `min_years` right? Run the ingestion
a second time — most jobs should report "unchanged."

**Then calibrate:**
```
Write scripts/calibrate.py as described in plan §6 (S_sem_raw of the fixture resumes against 50
random open jobs from unrelated categories), run it, and put the resulting SEM_BASELINE in config.
```

---

## Session 5 — Recommendations and applications

```
Read CLAUDE.md and docs/plan.md. This is Session 5. Do only this.

- Implement plan §9: GET /api/v1/recommendations with pgvector retrieval (top 100, filters in
  SQL), in-memory rerank with no external API calls, eligibility hiding/penalty, and the
  documented response shape.
- GET /api/v1/jobs/{id}.
- Finish POST /api/v1/analyses: support job_id (use stored job data) and job_url (Greenhouse
  and Lever posting URLs only, fetched through their public APIs; 422 for anything else).
- Applications CRUD from plan §11.
- DELETE /api/v1/account (deletes user rows, then the Supabase auth user using the secret key).
- Tests, then a timing check: call /recommendations for my fixture resume 5 times and report
  the latencies.
```

**You verify:** call `/recommendations` for your resume. Are the top 10 jobs ones you'd
actually consider? Does turning on `show_stretch` add senior roles? Save one job and move it
through the statuses.

---

## Session 6 — Frontend: auth, onboarding, analysis

```
Read CLAUDE.md and docs/plan.md. This is Session 6. Do only this.

Create frontend/ with Vite + React + TypeScript + Tailwind + React Router + supabase-js.
- An API client that attaches the Supabase access token, handles 401 by redirecting to /login,
  and shows the "waking up the server" state from plan §12.
- TypeScript types mirroring the backend Pydantic response models.
- Pages: /login, /signup, /onboarding, /dashboard, /analyze, /analyses/:id, /history, with
  protected-route and no-resume redirects.
- The analysis page shows the final score prominently, the three subscores with one-line
  explanations of what each measures, matched and missing skills, weak requirements, and the
  feedback items grouped by severity.
- Clean, simple, responsive. Loading and error states on every request.
- frontend/.env.example.

Run both servers and walk through signup → onboarding → dashboard → analyze yourself using the
browser tooling if available; otherwise give me a checklist to click through.
```

**You verify:** do the full flow as a brand-new user in a private browser window.

---

## Session 7 — Frontend: jobs feed and tracker

```
Read CLAUDE.md and docs/plan.md. This is Session 7. Do only this.

- /jobs: ranked job cards (title, company, location, remote badge, score, top matched and missing
  skills), filters for remote, location, level, and show-stretch, "Full analysis" (creates an
  analysis by job_id and navigates to it), and "Save".
- /jobs/:id: description, "Full analysis", "Save", and "Apply" opening the posting URL in a new tab.
- /tracker: saved jobs grouped by status with status changes and notes.
- /settings: list/delete resumes, set the active one, delete account with a confirmation step.
- Navigation across all pages.
```

**You verify:** find a job in the feed, run a full analysis, save it, apply (opens the real
posting), and mark it applied in the tracker.

---

## Session 8 — Deploy

```
Read CLAUDE.md and docs/plan.md. This is Session 8. Do only this.

- backend/Dockerfile (python:3.11-slim, installs requirements and the spaCy model, runs
  alembic upgrade head then uvicorn with 1 worker on $PORT) and .dockerignore. Report the
  image size.
- render.yaml for a free Docker web service with health check /health and the env var names.
- frontend/vercel.json with an SPA rewrite so deep links like /jobs/123 work.
- .github/workflows/ci.yml: backend pytest and frontend build on every PR and push.
- .github/workflows/ingest.yml: daily cron plus manual workflow_dispatch, running the
  ingestion with DATABASE_URL and VOYAGE_API_KEY from repository secrets.
- A DEPLOY.md with the exact dashboard steps for me.
```

**You do** (following DEPLOY.md):
1. **Render**: New → Blueprint (or Web Service from the repo), free instance, paste the
   backend env vars. Wait for the health check to pass. Note the URL.
2. **Vercel**: import the repo, root directory `frontend`, add the three `VITE_` variables
   (`VITE_API_BASE_URL` = your Render URL). Note the URL.
3. **Render**: add the Vercel URL to `CORS_ORIGINS`, redeploy.
4. **Supabase** → Authentication → URL Configuration: set Site URL to your Vercel URL.
5. **GitHub** → Settings → Secrets → Actions: add `DATABASE_URL` and `VOYAGE_API_KEY`.
   Run the ingest workflow once manually from the Actions tab.

**You verify:** on your phone, not your laptop: sign up, upload, analyze, browse jobs, save
one. That's the stack proven end to end on free tiers.

---

## If something breaks

Paste the exact error into Claude Code with where it happened. Common ones:

- **Render crashes on start / out of memory**: something pulled in PyTorch, or there's more
  than one worker. Check the image size and `requirements.txt`.
- **Can't connect to the database from Render or Actions**: you're using the direct
  connection string. Use the session pooler string.
- **CORS error in the browser**: the Vercel URL isn't in `CORS_ORIGINS` exactly (scheme
  included, no trailing slash).
- **Every job scores about the same**: calibration wasn't run, or S_sem is embedding whole
  documents instead of bullets.
- **401 on every request after deploy**: `SUPABASE_URL` differs between frontend and backend.
- **Deep links 404 on Vercel**: the `vercel.json` rewrite is missing.
- **First request takes a minute**: Render's free tier sleeping. Expected.
