# Deploying JobFit

Backend on Render (Docker, free), frontend on Vercel, ingestion on GitHub Actions. Everything
stays on free tiers. Expect about 30 minutes the first time.

Before you start, have these open:
- **Supabase** → your project → Project Settings → API keys, and Connect.
- **GitHub** → the repo (`tajb1d/JobFitTesting`), pushed and up to date.
- **Render** and **Vercel** accounts, both signed in with GitHub.

The values you'll copy between dashboards:

| Value | Where it comes from |
|---|---|
| `DATABASE_URL` | Supabase → Connect → **Session pooler** (IPv4), with `postgresql://` swapped for `postgresql+psycopg://` |
| `SUPABASE_URL` | Supabase → Project Settings → API keys → Project URL |
| `SUPABASE_SECRET_KEY` | Supabase → API keys → the `sb_secret_…` key. Server only |
| `VOYAGE_API_KEY` | Voyage dashboard |
| `ANTHROPIC_API_KEY` | Anthropic Console. Must be scoped to a workspace |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase → API keys → the `sb_publishable_…` key. Safe in the browser |

Your local `backend/.env` already holds the first five.

---

## 1. Render: the API

1. **Render → New → Blueprint**, pick this repo, and let it read `render.yaml`. It creates a
   free Docker web service called **jobfit-api** that builds `backend/Dockerfile`.
   (No Blueprint? Use **New → Web Service**, pick the repo, choose **Docker**, set the
   Dockerfile path to `backend/Dockerfile` and the Docker context to `backend`, and set the
   health check path to `/health`.)
2. When prompted, paste the six environment variables. `CORS_ORIGINS` has no Vercel URL yet,
   so set it to `http://localhost:5173` for now.
3. Deploy. The first build takes about 5 minutes: it installs the dependencies and the spaCy
   model. On start, migrations run before uvicorn does.
4. Wait for the health check to pass, then open `https://<your-service>.onrender.com/health`.
   It should return `{"status":"ok"}`. **Note that URL.**

## 2. Vercel: the frontend

1. **Vercel → Add New → Project**, import the repo.
2. Set **Root Directory** to `frontend`. Vercel detects Vite; `frontend/vercel.json` adds the
   rewrite that keeps deep links like `/jobs/<id>` working.
3. Add three environment variables:
   - `VITE_API_BASE_URL` = your Render URL, no trailing slash
   - `VITE_SUPABASE_URL` = your Supabase project URL
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = the `sb_publishable_…` key
4. Deploy, and **note the Vercel URL**.

## 3. Render again: allow the browser through

1. Render → jobfit-api → Environment → edit `CORS_ORIGINS` to include both:
   `http://localhost:5173,https://<your-app>.vercel.app`
2. Save. Render redeploys.

Exact match matters: scheme included, no trailing slash, no spaces.

## 4. Supabase: point auth at the deployed site

Supabase → Authentication → URL Configuration:
- **Site URL**: your Vercel URL.
- **Redirect URLs**: add `https://<your-app>.vercel.app/**`.

## 5. GitHub: CI and the daily ingestion

1. Repo → Settings → Secrets and variables → **Actions** → New repository secret. Add:
   - `DATABASE_URL` (the same session pooler string)
   - `VOYAGE_API_KEY`
2. Repo → **Actions** → "Ingest jobs" → **Run workflow**. Leave the inputs empty for a full
   run; set "Only fetch the first N companies" to 3 for a quick smoke test.
3. The run prints the summary: companies fetched, jobs new/updated/unchanged/closed, and the
   embedding tokens used.

CI (`.github/workflows/ci.yml`) needs no secrets: the tests that touch Supabase skip
themselves, so pushes and pull requests run the other 319 tests plus the frontend build.

---

## Check it end to end

On your phone, on mobile data rather than your home WiFi:

1. Open the Vercel URL and sign up with a new email.
2. Upload a resume PDF, confirm the suggested roles, and finish onboarding.
3. The dashboard shows your structure score.
4. Jobs → a ranked feed. Open one, run a full analysis, save it.
5. Tracker → the saved job. Change its status to Applied.

The very first request after an idle period takes up to a minute: Render's free instance
sleeps. The UI says "waking up the server" while that happens.

---

## When something breaks

| Symptom | Cause and fix |
|---|---|
| Render build fails at pip install | Check that nothing added torch or sentence-transformers. The image should stay around 1.5 GB |
| Render crashes on start, out of memory | More than one uvicorn worker. The entrypoint pins `--workers 1` |
| `could not translate host name` in Render or Actions logs | That's the direct connection string. Use the **session pooler** string, port 5432 |
| CORS error in the browser console | The Vercel URL isn't in `CORS_ORIGINS` exactly: scheme, no trailing slash. Redeploy after editing |
| 401 on every request after deploy | `VITE_SUPABASE_URL` and the backend's `SUPABASE_URL` point at different projects |
| Deep links 404 on Vercel | `frontend/vercel.json` missing, or the root directory isn't `frontend` |
| Analyses return template feedback, not Claude's | `ANTHROPIC_API_KEY` isn't scoped to a workspace. Render logs show "LLM feedback unavailable" |
| Ingestion is very slow, or 429s | Voyage rate limits. With a payment method on the account, the defaults in `app/config.py` (300 req/min, 1M tokens/min) apply |
| The feed is empty | No jobs ingested yet. Run the ingest workflow |

## Cost

Everything above is free: Render's free web service, Vercel's hobby plan, Supabase's free
project, GitHub Actions on a public repo, and Voyage's free token allowance. Anthropic usage
is pay-as-you-go, but an analysis costs a fraction of a cent, and the per-user limit of 20 per
hour caps it.

Two free-tier limits to know: Render sleeps after 15 minutes idle, so the first request is
slow, and Supabase pauses a project after a week with no activity, which the daily ingestion
prevents.
