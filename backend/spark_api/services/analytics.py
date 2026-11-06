"""Read-side assembly for the Analytics and Course Director pages."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from spark_api.services.queries import has_table
from spark_core.analytics.director import section_summary, student_health
from spark_core.etl.admissions import CATEGORY_FIELDS, NUMERIC_FIELDS, YESNO_FIELDS, enrich_with_admissions
from spark_core.etl.store import query_df


def load_clusters(db_file: Path) -> pd.DataFrame | None:
    """Features + risk cluster + name + section, one row per student. None if not clustered."""
    if not has_table(db_file, "STUDENT_CLUSTERS"):
        return None
    return query_df(db_file, """
        SELECT f.*, c.cluster, c.cluster_label, c.risk_rank, s.STUDENT_NAME, s.STUDENT_SORTABLE_NAME,
               ANY_VALUE(sec.SECTION_NAME) AS SECTION_NAME
        FROM STUDENT_FEATURES f
        JOIN STUDENT_CLUSTERS c USING (STUDENT_ID)
        JOIN DIM_STUDENTS s USING (STUDENT_ID)
        LEFT JOIN V_SECTION_MEMBERSHIP m USING (STUDENT_ID)
        LEFT JOIN DIM_SECTIONS sec USING (SECTION_ID)
        GROUP BY ALL
        ORDER BY c.risk_rank, s.STUDENT_SORTABLE_NAME""")


def with_admissions(clusters: pd.DataFrame, admissions_db: Path) -> tuple[pd.DataFrame, dict]:
    df, rate = enrich_with_admissions(clusters, admissions_db)
    info = {
        "available": rate > 0,
        "match_rate": rate,
        "matched": int(round(rate * len(df))),
        "numeric_fields": {k: v for k, v in NUMERIC_FIELDS.items() if k in df and df[k].notna().any()},
        "yesno_fields": {k: v for k, v in YESNO_FIELDS.items() if k in df and df[k].notna().any()},
        "category_fields": {k: v for k, v in CATEGORY_FIELDS.items() if k in df and df[k].notna().any()},
    }
    return df, info


def director_roster(db_file: Path) -> pd.DataFrame:
    """One row per (section, student) with features and per-student health index."""
    roster = query_df(db_file, """
        SELECT m.SECTION_ID, sec.SECTION_NAME, m.STUDENT_ID, s.STUDENT_NAME, s.STUDENT_SORTABLE_NAME,
               m.ENROLLMENT_STATE, m.CURRENT_SCORE, m.CURRENT_GRADE, f.* EXCLUDE (STUDENT_ID)
        FROM V_SECTION_MEMBERSHIP m
        JOIN DIM_SECTIONS sec USING (SECTION_ID)
        JOIN DIM_STUDENTS s USING (STUDENT_ID)
        LEFT JOIN STUDENT_FEATURES f USING (STUDENT_ID)""")
    if has_table(db_file, "STUDENT_CLUSTERS"):
        risk = query_df(db_file, "SELECT STUDENT_ID, cluster_label FROM STUDENT_CLUSTERS")
        roster = roster.merge(risk, on="STUDENT_ID", how="left")
    else:
        roster["cluster_label"] = None
    return student_health(roster)


def director_summary(db_file: Path, cutoff: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    roster = director_roster(db_file)
    return roster, section_summary(roster, cutoff_pct=cutoff)
