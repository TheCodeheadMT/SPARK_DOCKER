"""Success-model training, aliasing and prediction for the API."""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import select

from spark_api.db import Course, Database, IngestRun, ModelAlias, SuccessModel
from spark_core.etl.store import course_db_path, query_df
from spark_core.ids import slug
from spark_core.ml import success as S

_cache: dict[tuple, object] = {}
_cache_lock = threading.Lock()


def course_inputs(db_file: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    feats = query_df(db_file, "SELECT * FROM STUDENT_FEATURES")
    grades = query_df(db_file, """
        SELECT STUDENT_ID, CURRENT_SCORE FROM (
          SELECT STUDENT_ID, CURRENT_SCORE,
                 ROW_NUMBER() OVER (PARTITION BY STUDENT_ID ORDER BY UPDATED_AT DESC NULLS LAST) AS rn
          FROM FACT_ENROLLMENTS WHERE ENROLLMENT_TYPE ILIKE '%Student%') t WHERE rn = 1""")
    perf = query_df(db_file, "SELECT ASSIGNMENT_ID, POINTS_POSSIBLE FROM STUDENT_PERFORMANCE")
    return feats, grades, S.course_totals(perf)


def summarize(m: SuccessModel | None, course_names: dict[str, str] | None = None) -> dict | None:
    if m is None:
        return None
    return {"id": m.id, "course_id": m.course_id, "course_name": (course_names or {}).get(m.course_id, m.course_id),
            "trained_at": m.trained_at.isoformat(), "trained_by": m.trained_by, "config": m.config, **m.summary}


def train_for_course(db: Database, data_dir: Path, course: Course, cfg: S.SuccessModelConfig, user: str) -> SuccessModel:
    db_file = course_db_path(data_dir, course.id)
    feats, grades, totals = course_inputs(db_file)
    result = S.train(feats, grades, totals, cfg)          # raises S.TrainingError
    out_dir = data_dir / "models" / slug(course.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"success_{uuid.uuid4().hex[:12]}.joblib"
    joblib.dump(result["payload"], path)
    with db.session() as s:
        run = s.scalars(select(IngestRun).where(IngestRun.course_id == course.id, IngestRun.status == "succeeded")
                        .order_by(IngestRun.id.desc()).limit(1)).first()
        for old in s.scalars(select(SuccessModel).where(SuccessModel.course_id == course.id, SuccessModel.active)):
            old.active = False
        m = SuccessModel(course_id=course.id, trained_by=user, path=str(path), config=asdict(cfg), active=True,
                         ingest_run_id=run.id if run else None,
                         summary={"n_students": result["n_students"], "n_success": result["n_success"],
                                  "success_rate": result["success_rate"], "metrics": result["metrics"],
                                  "features": result["payload"]["feature_cols"],
                                  "importance": S.feature_importance(result["payload"])})
        s.add(m)
        s.commit()
        return m


def active_model(s, course_id: str) -> SuccessModel | None:
    return s.scalars(select(SuccessModel).where(SuccessModel.course_id == course_id, SuccessModel.active)
                     .order_by(SuccessModel.id.desc()).limit(1)).first()


def resolve(db: Database, course_id: str) -> tuple[SuccessModel | None, str | None]:
    """(model used for predictions in course_id, alias source course id or None)."""
    with db.session() as s:
        alias = s.get(ModelAlias, course_id)
        source = alias.source_course_id if alias else course_id
        return active_model(s, source), (alias.source_course_id if alias else None)


def set_alias(db: Database, course_id: str, source_course_id: str | None, user: str) -> None:
    with db.session() as s:
        cur = s.get(ModelAlias, course_id)
        if source_course_id in (None, "", course_id):
            if cur:
                s.delete(cur)
        elif cur:
            cur.source_course_id, cur.set_by = source_course_id, user
        else:
            s.add(ModelAlias(course_id=course_id, source_course_id=source_course_id, set_by=user))
        s.commit()


def _load_payload(path: str) -> dict:
    with _cache_lock:
        key = ("payload", path)
        if key not in _cache:
            _cache[key] = joblib.load(path)
        return _cache[key]  # type: ignore[return-value]


def predictions(db: Database, data_dir: Path, course_id: str) -> tuple[pd.Series | None, dict | None]:
    """P(success) for every student in course_id, using its own or its aliased model."""
    model, alias_source = resolve(db, course_id)
    if model is None or not Path(model.path).exists():
        return None, None
    db_file = course_db_path(data_dir, course_id)
    if not db_file.exists():
        return None, None
    key = ("pred", model.id, course_id, db_file.stat().st_mtime_ns)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        feats, _, totals = course_inputs(db_file)
        hit = S.predict(_load_payload(model.path), feats, totals)
        with _cache_lock:
            _cache[key] = hit
    with db.session() as s:
        names = {c.id: c.name for c in s.scalars(select(Course))}
    info = {"model_id": model.id, "source_course_id": model.course_id, "source_course_name": names.get(model.course_id),
            "aliased": alias_source is not None, "trained_at": model.trained_at.isoformat(),
            "threshold": model.config.get("success_threshold"), "metrics": model.summary.get("metrics")}
    return hit, info  # type: ignore[return-value]
