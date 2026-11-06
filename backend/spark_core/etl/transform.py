"""Ported from SPARK etl/transform.py.

transform_data() logic is unchanged from the Streamlit version so analytics stay
comparable. Datetime parsing is LMS-neutral (Canvas ISO strings or Blackboard
timestamps already parsed by the adapter).

enrich_clusters_with_admissions() and make_fl_name_key_from_student_name() will be
ported with the Analytics tab (Phase 4).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _parse_datetime_utc(series: pd.Series) -> pd.Series:
    if series is None:
        return series
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dt, "tz", None) is not None:
            return series.dt.tz_convert("UTC")
        return series.dt.tz_localize("UTC")
    return pd.to_datetime(series, errors="coerce", utc=True, cache=True)


def transform_data(raw: dict) -> pd.DataFrame:
    """Unify assignments + students + submissions into STUDENT_PERFORMANCE rows."""
    assignments = raw.get("assignments", pd.DataFrame())
    students = raw.get("students", pd.DataFrame())
    submissions = raw.get("submissions", pd.DataFrame())

    if submissions.empty:
        return pd.DataFrame()

    df = submissions.merge(assignments, on="ASSIGNMENT_ID", how="left")
    df = df.merge(students, on="STUDENT_ID", how="left")

    for col in ("SUBMITTED_AT", "GRADED_AT", "DUE_AT"):
        if col in df:
            df[col] = _parse_datetime_utc(df[col])

    df["SUBMISSION_DELTA_DAYS"] = (df["SUBMITTED_AT"] - df["DUE_AT"]).dt.total_seconds() / 86400.0
    df["POINTS_EARNED"] = pd.to_numeric(df["POINTS_EARNED"], errors="coerce").fillna(0)
    df["POINTS_POSSIBLE"] = pd.to_numeric(df["POINTS_POSSIBLE"], errors="coerce").fillna(0)
    df["LATE"] = df["LATE"].fillna(False).astype(bool) if "LATE" in df else False
    df["PERCENTAGE"] = np.where(df["POINTS_POSSIBLE"] > 0, df["POINTS_EARNED"] / df["POINTS_POSSIBLE"], np.nan)

    df = df.sort_values(["STUDENT_ID", "SUBMITTED_AT"])
    df["CUMULATIVE_POINTS"] = df.groupby("STUDENT_ID")["POINTS_EARNED"].cumsum()
    df["CUMULATIVE_POSSIBLE"] = df.groupby("STUDENT_ID")["POINTS_POSSIBLE"].cumsum()
    df["MISSED_ASSIGNMENT"] = df["POINTS_EARNED"].eq(0) & df["SUBMITTED_AT"].isna()
    df["ON_TIME"] = ~df["LATE"]

    df = df.rename(columns={"ASSIGNMENT_NAME": "ASSIGNMENT", "STUDENT_NAME": "STUDENT"})

    final_cols = [
        "STUDENT_ID", "STUDENT", "ASSIGNMENT_ID", "ASSIGNMENT", "DUE_AT", "SUBMITTED_AT", "GRADED_AT",
        "POINTS_EARNED", "POINTS_POSSIBLE", "PERCENTAGE", "CUMULATIVE_POINTS", "CUMULATIVE_POSSIBLE",
        "LATE", "ON_TIME", "MISSED_ASSIGNMENT", "SUBMISSION_DELTA_DAYS", "ASSIGNMENT_GROUP_ID",
        "COURSE_ID" if "COURSE_ID" in df else None,
    ]
    final_cols = [c for c in final_cols if c and c in df]
    return df[final_cols].reset_index(drop=True)
