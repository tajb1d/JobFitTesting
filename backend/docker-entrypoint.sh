#!/bin/sh
# Apply migrations, then serve. Both must use the same DATABASE_URL (the Supabase session
# pooler string). A failed migration stops the container instead of serving a stale schema.
set -e

echo "Running database migrations…"
alembic upgrade head

echo "Starting uvicorn on port ${PORT:-8000} (1 worker)…"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
