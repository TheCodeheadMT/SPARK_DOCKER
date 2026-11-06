"""Read-side queries over a course DuckDB, shaped for the API."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from spark_core.etl.store import query_df


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe records (NaN/NaT -> None, timestamps -> ISO)."""
    out = []
    for row in df.to_dict(orient="records"):
        clean = {}
        for k, v in row.items():
            if isinstance(v, pd.Timestamp):
                clean[k] = None if pd.isna(v) else v.isoformat()
            elif isinstance(v, (float, np.floating)):
                clean[k] = None if math.isnan(v) else float(v)
            elif isinstance(v, np.integer):
                clean[k] = int(v)
            elif isinstance(v, np.bool_):
                clean[k] = bool(v)
            elif v is pd.NaT or (not isinstance(v, (list, dict, str)) and pd.isna(v)):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out


def has_table(db_file: Path, name: str) -> bool:
    return bool(query_df(db_file, "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = ?",
                         [name])["n"].iat[0])


def stored_features(db_file: Path) -> pd.DataFrame:
    """Per-student features computed at ingest (STUDENT_FEATURES)."""
    return query_df(db_file, "SELECT * FROM STUDENT_FEATURES")


def risk_for(db_file: Path, student_id: str) -> str | None:
    if not has_table(db_file, "STUDENT_CLUSTERS"):
        return None
    r = query_df(db_file, "SELECT cluster_label FROM STUDENT_CLUSTERS WHERE STUDENT_ID = ?", [student_id])
    return None if r.empty else r["cluster_label"].iat[0]


def sections(db_file: Path) -> pd.DataFrame:
    return query_df(db_file, """
        SELECT s.SECTION_ID, s.SECTION_NAME, COUNT(m.STUDENT_ID) AS STUDENTS, ROUND(AVG(m.CURRENT_SCORE), 2) AS AVG_SCORE
        FROM DIM_SECTIONS s LEFT JOIN V_SECTION_MEMBERSHIP m USING (SECTION_ID)
        GROUP BY ALL ORDER BY s.SECTION_NAME""")


def roster(db_file: Path, section_id: str | None = None) -> pd.DataFrame:
    base = query_df(db_file, """
        SELECT st.STUDENT_ID, st.STUDENT_NAME, st.STUDENT_SORTABLE_NAME, m.SECTION_ID, sec.SECTION_NAME,
               m.CURRENT_SCORE, m.CURRENT_GRADE, m.ENROLLMENT_STATE
        FROM DIM_STUDENTS st
        JOIN V_SECTION_MEMBERSHIP m USING (STUDENT_ID)
        LEFT JOIN DIM_SECTIONS sec USING (SECTION_ID)
        WHERE (? IS NULL OR m.SECTION_ID = ?)
        ORDER BY st.STUDENT_SORTABLE_NAME""", [section_id, section_id])
    feats = stored_features(db_file)[["STUDENT_ID", "grade_ratio", "avg_percentage", "late_rate",
                                     "missed_rate", "num_assignments", "n_timed_items", "days_since_last_submission"]]
    return base.merge(feats, on="STUDENT_ID", how="left")


def student_rows(db_file: Path, student_id: str) -> pd.DataFrame:
    df = query_df(db_file, """
        SELECT p.*, a.DUE_SOURCE
        FROM STUDENT_PERFORMANCE p LEFT JOIN DIM_ASSIGNMENTS a USING (ASSIGNMENT_ID)
        WHERE p.STUDENT_ID = ?""", [student_id])
    if df.empty:
        return df
    df["EVENT_AT"] = df["SUBMITTED_AT"].fillna(df["DUE_AT"]).fillna(df["GRADED_AT"])
    return df.sort_values(["EVENT_AT", "ASSIGNMENT"]).reset_index(drop=True)


def student_profile(db_file: Path, student_id: str) -> dict | None:
    prof = query_df(db_file, """
        SELECT st.STUDENT_ID, st.STUDENT_NAME, m.SECTION_ID, sec.SECTION_NAME, m.CURRENT_SCORE, m.CURRENT_GRADE,
               m.UPDATED_AT AS LAST_ACCESS
        FROM DIM_STUDENTS st JOIN V_SECTION_MEMBERSHIP m USING (STUDENT_ID) LEFT JOIN DIM_SECTIONS sec USING (SECTION_ID)
        WHERE st.STUDENT_ID = ?""", [student_id])
    if prof.empty:
        return None
    rows = student_rows(db_file, student_id)
    feats = stored_features(db_file)
    f = feats[feats["STUDENT_ID"] == student_id]
    timed = rows[rows["SUBMITTED_AT"].notna()]
    kpis = {
        "points_earned": float(rows["POINTS_EARNED"].sum()),
        "points_possible": float(rows["POINTS_POSSIBLE"].sum()),
        "grade_ratio": float(f["grade_ratio"].iloc[0]) if len(f) else None,
        "items_graded": int(len(rows)),
        "items_missed": int(rows["MISSED_ASSIGNMENT"].sum()),
        "items_with_submit_time": int(len(timed)),
        "late": int(timed["LATE"].sum()),
        "avg_days_before_due": (float(-timed["SUBMISSION_DELTA_DAYS"].mean())
                                if timed["SUBMISSION_DELTA_DAYS"].notna().any() else None),
        "class_rank_pct": (float((feats["grade_ratio"] <= f["grade_ratio"].iloc[0]).mean())
                           if len(f) and pd.notna(f["grade_ratio"].iloc[0]) else None),
    }
    kpis["risk_label"] = risk_for(db_file, student_id)
    return {"profile": records(prof)[0], "kpis": kpis}
