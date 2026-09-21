# JobFit

Resume analyzer + job recommender. Full design lives in `docs/plan.md` — read it
before starting any feature. The build sequence lives in `docs/build-guide.md`.
Only do the session you are asked to do.

## Stack (decided — do not substitute)
- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, psycopg 3
- DB + auth: Supabase (Postgres + pgvector + Supabase Auth). No Firebase. No Flask.
- Frontend: React + Vite + TypeScript + Tailwind, React Router, supabase-js
- Embeddings: Voyage API behind `app/services/embeddings.py` only
- LLM feedback: Anthropic API, model `claude-haiku-4-5-20251001`, behind `app/services/feedback.py` only
- Hosting: Render (backend, Docker, free tier), Vercel (frontend), GitHub Actions (CI + daily ingestion)

## Conventions
- Routes in `app/api/` stay thin: parse, call services, return. Logic lives in `app/services/`.
- Every request/response body has a Pydantic schema in `app/schemas/`. No raw dicts from endpoints.
- Every query touching user data filters by `user_id` from the auth dependency. The backend
  connects as a privileged DB role, so RLS does NOT protect us from our own bugs.
- Services are plain functions, unit-testable without a server. Mock Voyage and Anthropic in tests.
- Schema changes only through Alembic migrations. Never edit the DB by hand.
- Config via `pydantic-settings` in `app/config.py`. Scoring weights live in config, not code.
- Run `pytest` from `backend/` and make it pass before saying a task is done.
- Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`

## Gotchas
- Never commit `.env`. Update `.env.example` (empty values) when adding a variable.
- `python-multipart` must be installed or file uploads fail.
- spaCy's built-in NER does NOT find skills. Skills come from `app/data/skills.json` via PhraseMatcher.
- Load the spaCy model and skill matcher once at startup, not per request.
- Render free tier has 512 MB RAM: one uvicorn worker, no PyTorch, no sentence-transformers.
- Use the Supabase *session pooler* connection string (IPv4). The direct string may fail from Render and GitHub Actions.
- Precompute everything about jobs at ingestion. At request time, only the resume is new.
- Feedback suggests additions the user may genuinely have. It never invents experience or rewrites the resume.
- We never auto-submit applications. "Apply" opens the posting's real URL.
