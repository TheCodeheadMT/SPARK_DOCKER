"""Ingest: LMS adapter -> per-course DuckDB, recorded as an IngestRun.

Phase 1 runs ingest in a background thread inside the API process, serialised per
course with a lock. Phase 5 moves the same function onto an RQ/arq worker so API
processes never write DuckDB files at all.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from adapters.base import CourseRef, LMSAdapter
from spark_api.db import Course, Database, IngestRun, utcnow
from spark_core.etl.store import course_db_path, write_course_db
from spark_core.etl.transform import transform_data
from spark_core.ml.clustering import train_clusters
from spark_core.ml.features import build_features_df

log = logging.getLogger("spark.ingest")
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

# Bump when build_tables() output changes, so unchanged inputs still get rebuilt once.
SCHEMA_VERSION = 3   # 3: no-section rows dropped; current-grade fallback


class IngestBusy(Exception):
    pass


def snapshot_time(perf: pd.DataFrame) -> pd.Timestamp | None:
    """Latest activity in the data: the moment this export describes."""
    stamps = [perf[c].max() for c in ("SUBMITTED_AT", "GRADED_AT") if c in perf and perf[c].notna().any()]
    return max(stamps) if stamps else None


def build_tables(course_id: str, data) -> tuple[dict[str, pd.DataFrame], dict]:
    perf = transform_data({
        "assignments": data.assignments.drop(columns=["DUE_SOURCE"], errors="ignore"),
        "students": data.students[["STUDENT_ID", "STUDENT_NAME"]],
        "submissions": data.submissions,
    })
    perf["COURSE_ID"] = course_id
    tables = {
        "DIM_SECTIONS": data.sections,
        "DIM_STUDENTS": data.students,
        "FACT_ENROLLMENTS": data.enrollments,
        "DIM_ASSIGNMENTS": data.assignments,
        "STUDENT_PERFORMANCE": perf,
    }
    extra: dict = {"schema": SCHEMA_VERSION}
    if not perf.empty:
        as_of = snapshot_time(perf)
        feats = build_features_df(perf, as_of=as_of.to_pydatetime() if as_of is not None else None)
        tables["STUDENT_FEATURES"] = feats
        extra["features_as_of"] = as_of.isoformat() if as_of is not None else None
        clusters, note = train_clusters(feats)
        extra["clusters"] = note
        if clusters is not None:
            tables["STUDENT_CLUSTERS"] = clusters[["STUDENT_ID", "cluster", "cluster_label", "risk_rank"]]
            extra["risk_counts"] = {k: int(v) for k, v in clusters["cluster_label"].value_counts().items()}
    return tables, extra


def last_success(db: Database, course_id: str) -> IngestRun | None:
    with db.session() as s:
        return s.scalars(select(IngestRun).where(IngestRun.course_id == course_id, IngestRun.status == "succeeded")
                         .order_by(IngestRun.id.desc()).limit(1)).first()


def run_ingest(db: Database, adapter: LMSAdapter, course: Course, data_dir: Path,
               triggered_by: str, force: bool = False) -> IngestRun:
    lock = _locks[course.id]
    if not lock.acquire(blocking=False):
        raise IngestBusy(course.id)
    try:
        ref = CourseRef(course.id, course.name, course.lms, course.source)
        with db.session() as s:
            run = IngestRun(course_id=course.id, status="running", triggered_by=triggered_by)
            s.add(run)
            s.commit()
            run_id = run.id
        try:
            version = adapter.source_version(ref)
            prev = last_success(db, course.id)
            db_file = course_db_path(data_dir, course.id)
            same_schema = bool(prev and (prev.report or {}).get("schema") == SCHEMA_VERSION)
            if (not force and prev and same_schema and prev.source_version == version and version != "live"
                    and db_file.exists()):
                status, report, error = "skipped", {**(prev.report or {}), "note": "inputs unchanged"}, ""
            else:
                data = adapter.load_course(ref)
                version = data.source_version
                tables, extra = build_tables(course.id, data)
                write_course_db(db_file, tables)
                status, report, error = "succeeded", {**data.report, **extra}, ""
        except Exception as exc:  # recorded, surfaced in the UI
            log.exception("ingest failed for %s", course.id)
            status, report, error, version = "failed", {}, f"{type(exc).__name__}: {exc}", ""
        with db.session() as s:
            run = s.get(IngestRun, run_id)
            run.status, run.report, run.error = status, report, error
            run.source_version, run.finished_at = version, utcnow()
            s.commit()
            return run
    finally:
        lock.release()
