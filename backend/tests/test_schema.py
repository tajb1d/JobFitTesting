"""Guards on the migrated schema that the ORM can't express on its own."""

import pytest
from sqlalchemy import text

from app.config import EMBEDDING_DIM

pytestmark = pytest.mark.db

TABLES = [
    "profiles", "resumes", "resume_bullets", "companies",
    "jobs", "job_requirements", "analyses", "applications", "alembic_version",
]
VECTOR_COLUMNS = [
    ("resumes", "embedding"),
    ("resume_bullets", "embedding"),
    ("jobs", "embedding"),
    ("job_requirements", "embedding"),
]


def test_rls_enabled_on_every_table(db_session):
    rows = dict(
        db_session.execute(
            text(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(:t)"
            ),
            {"t": TABLES},
        ).all()
    )
    assert set(rows) == set(TABLES)
    assert all(rows.values()), {t: on for t, on in rows.items() if not on}


def test_no_rls_policies(db_session):
    # Plan §10: RLS on with *no* policies, so the public REST API can read nothing.
    count = db_session.execute(
        text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
    ).scalar()
    assert count == 0


@pytest.mark.parametrize("table,column", VECTOR_COLUMNS)
def test_vector_dim_matches_config(db_session, table, column):
    col_type = db_session.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = CAST(:t AS regclass) AND attname = :c"
        ),
        {"t": f"public.{table}", "c": column},
    ).scalar()
    assert col_type == f"vector({EMBEDDING_DIM})"


def test_jobs_embedding_has_hnsw_cosine_index(db_session):
    indexdef = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_jobs_embedding_hnsw'")
    ).scalar()
    assert indexdef is not None
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef
