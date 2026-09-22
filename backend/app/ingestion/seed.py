"""Upsert companies from app/data/companies.json. Runnable alone (python -m
app.ingestion.seed); app.ingestion.run also calls it first, so new entries are picked up."""

import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Company

COMPANIES_FILE = Path(__file__).resolve().parent.parent / "data" / "companies.json"


def load_company_file(path: Path = COMPANIES_FILE) -> list[dict]:
    rows = json.loads(path.read_text())
    for row in rows:
        if row["ats"] not in ("greenhouse", "lever") or not row["board_token"] or not row["name"]:
            raise ValueError(f"bad company entry: {row}")
    return rows


def seed_companies(db: Session, rows: list[dict]) -> int:
    """Insert new companies and refresh names. Leaves active / consecutive_failures alone, so
    re-seeding never revives a board that ingestion deactivated."""
    if not rows:
        return 0
    stmt = insert(Company).values(
        [{"name": r["name"], "ats": r["ats"], "board_token": r["board_token"]} for r in rows]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Company.ats, Company.board_token], set_={"name": stmt.excluded.name}
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


def main() -> None:
    with get_sessionmaker()() as db:
        count = seed_companies(db, load_company_file())
    print(f"Seeded {count} companies from {COMPANIES_FILE.name}")


if __name__ == "__main__":
    main()
