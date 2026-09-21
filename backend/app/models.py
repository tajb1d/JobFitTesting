"""SQLAlchemy models for every table in plan §10.

The schema itself is owned by Alembic migrations; these models must stay in sync with them.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import EMBEDDING_DIM

LEVELS = "('intern', 'entry', 'mid', 'senior', 'staff')"

# Scores are unbounded NUMERIC in the DB but read back as float, not Decimal.
Score = Numeric(asdecimal=False)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# Supabase-managed. Declared only so foreign keys to auth.users resolve.
# Alembic's env.py excludes the auth schema, so migrations never touch it.
auth_users = Table(
    "users",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    schema="auth",
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )


def _bigint_pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _user_fk(index: bool = True) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
        index=index,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(f"experience_level IN {LEVELS}", name="experience_level"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'")
    )
    location: Mapped[str | None] = mapped_column(Text)
    remote_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    experience_level: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    structure_score: Mapped[float | None] = mapped_column(Score)
    structure_checks: Mapped[list[Any] | None] = mapped_column(JSONB)
    general_feedback: Mapped[list[Any] | None] = mapped_column(JSONB)
    suggested_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'")
    )
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)  # numpy.ndarray | None
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    created_at: Mapped[datetime] = _created_at()


class ResumeBullet(Base):
    __tablename__ = "resume_bullets"

    id: Mapped[int] = _bigint_pk()
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)  # numpy.ndarray | None


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("ats", "board_token"),
        CheckConstraint("ats IN ('greenhouse', 'lever')", name="ats"),
    )

    id: Mapped[int] = _bigint_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ats: Mapped[str] = mapped_column(Text, nullable=False)
    board_token: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "external_id"),
        CheckConstraint("status IN ('open', 'closed')", name="status"),
        CheckConstraint(f"level IN {LEVELS}", name="level"),
        Index(
            "ix_jobs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    is_remote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    level: Mapped[str | None] = mapped_column(Text)
    min_years: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)  # numpy.ndarray | None
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'open'"), index=True
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobRequirement(Base):
    __tablename__ = "job_requirements"

    id: Mapped[int] = _bigint_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)  # numpy.ndarray | None


class Analysis(Base):
    __tablename__ = "analyses"
    # History is listed newest-first per user.
    __table_args__ = (Index("ix_analyses_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk(index=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL when the user pasted a description instead of picking an ingested job.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    job_title: Mapped[str | None] = mapped_column(Text)
    job_description_text: Mapped[str] = mapped_column(Text, nullable=False)
    final_score: Mapped[float | None] = mapped_column(Score)
    skill_score: Mapped[float | None] = mapped_column(Score)
    semantic_score: Mapped[float | None] = mapped_column(Score)
    structure_score: Mapped[float | None] = mapped_column(Score)
    tfidf_score: Mapped[float | None] = mapped_column(Score)
    matched_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    missing_skills: Mapped[list[Any] | None] = mapped_column(JSONB)
    weak_requirements: Mapped[list[Any] | None] = mapped_column(JSONB)
    feedback_items: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id"),
        CheckConstraint(
            "status IN ('saved', 'applied', 'interviewing', 'offer', 'rejected')",
            name="status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # UNIQUE(user_id, job_id) already indexes user_id.
    user_id: Mapped[uuid.UUID] = _user_fk(index=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'saved'"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
