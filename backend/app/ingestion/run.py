"""Entry point: python -m app.ingestion.run [--limit-companies N]"""

import argparse
import logging
import sys
import time

from app.config import get_settings
from app.db import get_sessionmaker
from app.ingestion.pipeline import run_ingestion
from app.ingestion.seed import load_company_file, seed_companies
from app.ingestion.sources import fetch_board, make_client
from app.models import Company
from app.services.embeddings import VoyageProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest jobs from Greenhouse and Lever boards.")
    parser.add_argument("--limit-companies", type=int, default=None, metavar="N",
                        help="only fetch the first N active companies (for testing)")
    parser.add_argument("--reparse", action="store_true",
                        help="re-parse unchanged jobs too (after parser or skills.json changes);"
                             " re-embeds only requirement lists that changed")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    embedder = VoyageProvider(
        settings.voyage_api_key,
        requests_per_minute=settings.voyage_requests_per_minute,
        tokens_per_minute=settings.voyage_tokens_per_minute,
        max_retries=5,
        rate_limit_backoff=30,
    )
    client = make_client()

    def fetch(company: Company):
        time.sleep(settings.ingestion_request_delay)
        logging.getLogger("app.ingestion").info("fetching %s (%s)", company.name, company.ats)
        return fetch_board(client, company.ats, company.board_token)

    with get_sessionmaker()() as db:
        seed_companies(db, load_company_file())
        summary = run_ingestion(db, embedder, fetch, settings, args.limit_companies, args.reparse)
    print(summary.render())
    return 1 if summary.error else 0


if __name__ == "__main__":
    sys.exit(main())
