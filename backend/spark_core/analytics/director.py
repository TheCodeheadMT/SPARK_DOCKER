"""Ported from SPARK course_director.py (section ranking + health index).

Logic kept: roster-first (a student in several sections counts in each), grade from
the enrollment CURRENT_SCORE when present (else grade_ratio), and the same Health
Index weights. Fix: metrics where lower is better (late, missed, days since last
activity) now rank ascending; the Streamlit version ranked them as if higher were better.
"""
from __future__ import annotations

import pandas as pd

HEALTH_WEIGHTS = {"grade": 0.65, "coverage": 0.15, "on_time": 0.10, "not_missed": 0.05, "recency": 0.05}
RECENCY_CAP_DAYS = 30

METRICS = {
    "HEALTH_INDEX": {"label": "Health Index (overall)", "higher_is_better": True},
    "MEAN_GRADE_PCT": {"label": "Mean grade (%)", "higher_is_better": True},
    "MEDIAN_GRADE_PCT": {"label": "Median grade (%)", "higher_is_better": True},
    "AT_RISK_PCT": {"label": "Students below cutoff (%)", "higher_is_better": False},
    "LATE_RATE": {"label": "Late submission rate", "higher_is_better": False},
    "MISSED_RATE": {"label": "Missed item rate", "higher_is_better": False},
    "DAYS_SINCE_LAST": {"label": "Days since last submission", "higher_is_better": False},
}


def health_index(grade_pct: pd.Series, coverage: pd.Series, late: pd.Series, missed: pd.Series,
                 days_since: pd.Series) -> pd.Series:
    w = HEALTH_WEIGHTS
    grade = grade_pct.fillna(0).clip(0, 100) / 100.0
    recency = days_since.fillna(RECENCY_CAP_DAYS).clip(0, RECENCY_CAP_DAYS) / RECENCY_CAP_DAYS
    return (w["grade"] * grade + w["coverage"] * coverage.fillna(0).clip(0, 1)
            + w["on_time"] * (1 - late.fillna(0).clip(0, 1)) + w["not_missed"] * (1 - missed.fillna(0).clip(0, 1))
            + w["recency"] * (1 - recency))


def student_health(roster: pd.DataFrame) -> pd.DataFrame:
    """roster: one row per (section, student) with CURRENT_SCORE + feature columns."""
    df = roster.copy()
    df["GRADE_PCT"] = df["CURRENT_SCORE"].astype(float)
    if df["GRADE_PCT"].isna().all():
        df["GRADE_PCT"] = df["grade_ratio"] * 100.0
    df["HAS_ACTIVITY"] = (df["num_assignments"].fillna(0) > 0).astype(float)
    df["HEALTH_INDEX"] = health_index(df["GRADE_PCT"], df["HAS_ACTIVITY"], df["late_rate"], df["missed_rate"],
                                      df["days_since_last_submission"])
    return df


def section_summary(roster: pd.DataFrame, cutoff_pct: float = 60.0) -> pd.DataFrame:
    df = student_health(roster)
    agg = df.groupby(["SECTION_ID", "SECTION_NAME"], dropna=False).agg(
        N_STUDENTS=("STUDENT_ID", "nunique"),
        COVERAGE_RATE=("HAS_ACTIVITY", "mean"),
        MEAN_GRADE_PCT=("GRADE_PCT", "mean"),
        MEDIAN_GRADE_PCT=("GRADE_PCT", "median"),
        AT_RISK_PCT=("GRADE_PCT", lambda s: float((s < cutoff_pct).mean() * 100.0)),
        LATE_RATE=("late_rate", "mean"),
        MISSED_RATE=("missed_rate", "mean"),
        DAYS_SINCE_LAST=("days_since_last_submission", "mean"),
    ).reset_index()
    agg["SECTION_NAME"] = agg["SECTION_NAME"].fillna("(Unknown section)")
    agg["HEALTH_INDEX"] = health_index(agg["MEAN_GRADE_PCT"], agg["COVERAGE_RATE"], agg["LATE_RATE"],
                                       agg["MISSED_RATE"], agg["DAYS_SINCE_LAST"])
    return agg


def rank_sections(summary: pd.DataFrame, metric: str) -> pd.DataFrame:
    if metric not in METRICS:
        raise ValueError(f"Unknown metric {metric!r}")
    asc = not METRICS[metric]["higher_is_better"]
    out = summary.copy()
    out["RANK"] = out[metric].rank(ascending=asc, method="min").astype("Int64")
    return out.sort_values(["RANK", "SECTION_NAME"]).reset_index(drop=True)
