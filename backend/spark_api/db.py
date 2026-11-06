"""Metadata database (SQLite in dev, PostgreSQL in production).

Replaces the Streamlit app's loose files: .streamlit/last_model_trained_*.txt,
refresh_config_*.json, success_model_aliases.json and per-session settings.
Student performance data does NOT live here; it stays in per-course DuckDB files.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)          # namespaced, e.g. bb:...
    name: Mapped[str] = mapped_column(String(300))
    lms: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseAccess(Base):
    """Who may see which course. Admins see everything without rows here."""
    __tablename__ = "course_access"
    __table_args__ = (UniqueConstraint("upn", "course_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upn: Mapped[str] = mapped_column(String(320), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(40))                             # Instructor | CourseDirector


class CoursePref(Base):
    """One user's own arrangement of their course list: last opened, manual position, archived."""
    __tablename__ = "course_prefs"
    upn: Mapped[str] = mapped_column(String(320), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)       # set by "Arrange"; lower = earlier
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiddenCourse(Base):
    """A user removed this course from their course list. Their access is suspended (404) until they restore it.
    Kept separate from CourseAccess so grant seeding (simulated personas, later Entra groups) can't undo it."""
    __tablename__ = "hidden_courses"
    upn: Mapped[str] = mapped_column(String(320), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))                           # running | succeeded | failed | skipped
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_version: Mapped[str] = mapped_column(String(64), default="")
    triggered_by: Mapped[str] = mapped_column(String(320), default="")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class AuditEvent(Base):
    """Who looked at which student's record (student-data accountability)."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    upn: Mapped[str] = mapped_column(String(320), index=True)
    action: Mapped[str] = mapped_column(String(60))
    course_id: Mapped[str] = mapped_column(String(200), default="")
    target: Mapped[str] = mapped_column(String(200), default="")


class SuccessModel(Base):
    """A trained success model. The file holds the sklearn pipeline (joblib), written only by this app."""
    __tablename__ = "success_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trained_by: Mapped[str] = mapped_column(String(320), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    path: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)      # n_students, success_rate, metrics, features
    ingest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ModelAlias(Base):
    """Course `course_id` predicts with the active model of `source_course_id` (cross-course reuse)."""
    __tablename__ = "model_aliases"
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    source_course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    set_by: Mapped[str] = mapped_column(String(320), default="")
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    upn: Mapped[str] = mapped_column(String(320), primary_key=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)


class SourceWatch(Base):
    """What the mount poller last saw for a course (cheap stat fingerprint, not content)."""
    __tablename__ = "source_watch"
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="new")   # see services/poller.py
    detail: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    seen_token: Mapped[str] = mapped_column(String(32), default="")      # fingerprint at the last check
    seen_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_token: Mapped[str] = mapped_column(String(32), default="")  # fingerprint of the last ingest
    failed_token: Mapped[str] = mapped_column(String(32), default="")    # don't retry a broken export
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EISession(Base):
    """A saved Extra Instruction session: inputs and generated plan/workbook for one student."""
    __tablename__ = "ei_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(String(200), index=True)
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    objectives: Mapped[str] = mapped_column(Text, default="")
    instructor_notes: Mapped[str] = mapped_column(Text, default="")
    student_notes: Mapped[str] = mapped_column(Text, default="")
    plan_md: Mapped[str] = mapped_column(Text, default="")
    workbook_md: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    temperature: Mapped[float] = mapped_column(default=0.3)
    workbook_config: Mapped[dict] = mapped_column(JSON, default=dict)
    modules: Mapped[list] = mapped_column(JSON, default=list)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)


class Database:
    def __init__(self, url: str):
        kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine = create_engine(url, **kw)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        # Alembic migrations replace this before the first production deploy.
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.SessionLocal()
