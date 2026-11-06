"""Ported from SPARK ml/features.py.

Changes from the Streamlit version:
  * build_features_df() works on an in-memory STUDENT_PERFORMANCE frame, so ingest can
    compute features once and store them (STUDENT_FEATURES). build_features(db_path)
    still exists for ad-hoc use and opens the DB read-only.
  * Lateness rates are computed only over items that HAVE a submission time.
    Blackboard quizzes/exams entered by instructor override have no submission time;
    counting them as "on time" would inflate on_time_rate. Canvas data (every
    submission timed) is unaffected.
  * n_timed_items lets models tell "no lateness data" from "never late".
  * days_since_last_submission is measured against `as_of` (the ingest time), so it
    describes the data snapshot rather than drifting between ingests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

FEATURE_SQL = """
SELECT
    STUDENT_ID,
    SUM(POINTS_EARNED)   AS total_points,
    SUM(POINTS_POSSIBLE) AS total_possible,
    CASE WHEN SUM(POINTS_POSSIBLE) > 0 THEN SUM(POINTS_EARNED) / SUM(POINTS_POSSIBLE) END AS grade_ratio,

    AVG(CASE WHEN SUBMITTED_AT IS NOT NULL THEN CASE WHEN LATE THEN 1 ELSE 0 END END)    AS late_rate,
    AVG(CASE WHEN SUBMITTED_AT IS NOT NULL THEN CASE WHEN ON_TIME THEN 1 ELSE 0 END END) AS on_time_rate,
    AVG(CASE WHEN MISSED_ASSIGNMENT THEN 1 ELSE 0 END)                                   AS missed_rate,
    COUNT(SUBMITTED_AT)                                                                  AS n_timed_items,

    AVG(PERCENTAGE)         AS avg_percentage,
    STDDEV_SAMP(PERCENTAGE) AS std_percentage,

    COUNT(*)          AS num_assignments,
    MIN(SUBMITTED_AT) AS min_date,
    MAX(SUBMITTED_AT) AS max_date,

    AVG(DATEDIFF('day', DUE_AT, SUBMITTED_AT)) AS mean_submission_delta_days,
    DATEDIFF('day', MAX(SUBMITTED_AT), CAST(? AS TIMESTAMP)) AS days_since_last_submission
FROM perf
GROUP BY STUDENT_ID
"""


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df["has_due_dates"] = df["mean_submission_delta_days"].notna().astype(int)
    df["mean_submission_delta_days"] = df["mean_submission_delta_days"].fillna(0.0)
    df["late_rate"] = df["late_rate"].fillna(0.0)
    df["on_time_rate"] = df["on_time_rate"].fillna(1.0)
    df["std_percentage"] = df["std_percentage"].fillna(0.0)
    span_days = (pd.to_datetime(df["max_date"]) - pd.to_datetime(df["min_date"])).dt.days
    weeks_active = (span_days / 7).clip(lower=1).fillna(1)
    df["pace"] = df["num_assignments"] / weeks_active
    df["weeks_active"] = weeks_active
    return df


def build_features_df(perf: pd.DataFrame, as_of: datetime | None = None) -> pd.DataFrame:
    as_of = (as_of or datetime.now(timezone.utc)).replace(tzinfo=None)
    p = perf.copy()
    for c in ("SUBMITTED_AT", "DUE_AT", "GRADED_AT"):
        if c in p and getattr(p[c].dt, "tz", None) is not None:
            p[c] = p[c].dt.tz_convert("UTC").dt.tz_localize(None)
    con = duckdb.connect()
    try:
        con.register("perf", p)
        df = con.execute(FEATURE_SQL, [as_of]).fetchdf()
    finally:
        con.close()
    return _finish(df)


def build_features(db_path: str | Path, as_of: datetime | None = None) -> pd.DataFrame:
    as_of = (as_of or datetime.now(timezone.utc)).replace(tzinfo=None)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(FEATURE_SQL.replace("FROM perf", "FROM STUDENT_PERFORMANCE"), [as_of]).fetchdf()
    finally:
        con.close()
    return _finish(df)
