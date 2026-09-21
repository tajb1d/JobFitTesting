"""Initial schema: pgvector, all tables, indexes, RLS.

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at the value this migration was written with. Do NOT import it from config:
# changing EMBEDDING_DIM later must come with a new migration, not rewrite this one.
EMBEDDING_DIM = 512

LEVELS = "('intern', 'entry', 'mid', 'senior', 'staff')"

TABLES = [
    "profiles",
    "resumes",
    "resume_bullets",
    "companies",
    "jobs",
    "job_requirements",
    "analyses",
    "applications",
]


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _bigint_pk() -> sa.Column:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def _user_fk() -> sa.Column:
    return sa.Column(
        "user_id",
        UUID(as_uuid=True),
        sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "profiles",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_roles", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("location", sa.Text),
        sa.Column("remote_ok", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("experience_level", sa.Text),
        _now("updated_at"),
        sa.CheckConstraint(
            f"experience_level IN {LEVELS}", name=op.f("ck_profiles_experience_level")
        ),
    )

    op.create_table(
        "resumes",
        _uuid_pk(),
        _user_fk(),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("sections", JSONB),
        sa.Column("skills", JSONB),
        sa.Column("structure_score", sa.Numeric),
        sa.Column("structure_checks", JSONB),
        sa.Column("general_feedback", JSONB),
        sa.Column("suggested_roles", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _now("created_at"),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])

    op.create_table(
        "resume_bullets",
        _bigint_pk(),
        sa.Column(
            "resume_id",
            UUID(as_uuid=True),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.Text),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
    )
    op.create_index("ix_resume_bullets_resume_id", "resume_bullets", ["resume_id"])

    op.create_table(
        "companies",
        _bigint_pk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("ats", sa.Text, nullable=False),
        sa.Column("board_token", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("ats", "board_token", name="uq_companies_ats_board_token"),
        sa.CheckConstraint("ats IN ('greenhouse', 'lever')", name=op.f("ck_companies_ats")),
    )

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column("company_id", sa.BigInteger, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("location", sa.Text),
        sa.Column("is_remote", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("description_text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("skills", JSONB),
        sa.Column("level", sa.Text),
        sa.Column("min_years", sa.Integer),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        _now("first_seen"),
        _now("last_seen"),
        sa.UniqueConstraint("company_id", "external_id", name="uq_jobs_company_id_external_id"),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_jobs_status")),
        sa.CheckConstraint(f"level IN {LEVELS}", name=op.f("ck_jobs_level")),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index(
        "ix_jobs_embedding_hnsw",
        "jobs",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "job_requirements",
        _bigint_pk(),
        sa.Column(
            "job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("section", sa.Text),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
    )
    op.create_index("ix_job_requirements_job_id", "job_requirements", ["job_id"])

    op.create_table(
        "analyses",
        _uuid_pk(),
        _user_fk(),
        sa.Column(
            "resume_id",
            UUID(as_uuid=True),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("job_title", sa.Text),
        sa.Column("job_description_text", sa.Text, nullable=False),
        sa.Column("final_score", sa.Numeric),
        sa.Column("skill_score", sa.Numeric),
        sa.Column("semantic_score", sa.Numeric),
        sa.Column("structure_score", sa.Numeric),
        sa.Column("tfidf_score", sa.Numeric),
        sa.Column("matched_skills", JSONB),
        sa.Column("missing_skills", JSONB),
        sa.Column("weak_requirements", JSONB),
        sa.Column("feedback_items", JSONB),
        _now("created_at"),
    )
    op.create_index("ix_analyses_user_id_created_at", "analyses", ["user_id", "created_at"])
    op.create_index("ix_analyses_resume_id", "analyses", ["resume_id"])
    op.create_index("ix_analyses_job_id", "analyses", ["job_id"])

    op.create_table(
        "applications",
        _uuid_pk(),
        _user_fk(),
        sa.Column(
            "job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'saved'")),
        sa.Column("notes", sa.Text),
        _now("created_at"),
        _now("updated_at"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_id_job_id"),
        sa.CheckConstraint(
            "status IN ('saved', 'applied', 'interviewing', 'offer', 'rejected')",
            name=op.f("ck_applications_status"),
        ),
    )
    op.create_index("ix_applications_job_id", "applications", ["job_id"])

    # RLS on, no policies: blocks the auto-generated REST API (publishable key) from reading
    # anything. The backend's privileged role bypasses RLS, so our own queries must still
    # filter by user_id.
    # alembic_version also lives in public, so it gets the same treatment. Never FORCE RLS:
    # that would apply it to the owning role and lock the backend out.
    for table in [*TABLES, "alembic_version"]:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    # The vector extension is left installed; other schemas may depend on it.
