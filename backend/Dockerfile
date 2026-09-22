# JobFit API. Build context is backend/.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first: this layer is cached until the pinned versions change. The spaCy model
# is a pinned wheel URL inside requirements.txt, so it installs here too. Every dependency
# ships a manylinux wheel, so no compiler is needed in the image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 10001 jobfit && chown -R jobfit:jobfit /app
USER jobfit

# Render sets $PORT; 8000 keeps `docker run -p 8000:8000` working locally.
ENV PORT=8000
EXPOSE 8000

# Migrations run at start, then one uvicorn worker: the free instance has 512 MB, and the
# spaCy model plus the skill matcher are loaded once per worker.
CMD ["./docker-entrypoint.sh"]
