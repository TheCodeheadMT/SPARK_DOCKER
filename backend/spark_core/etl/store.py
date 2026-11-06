"""Per-course DuckDB storage (replaces load_duckdb.py helpers for the web app).

Concurrency rule: many API processes may READ a course DB at once, but only the
ingest worker WRITES, and it never writes in place. It builds a complete new DB in
a temp file and atomically swaps it in with os.replace(). Readers open read-only
connections per request and so always see either the old or the new file, never a
half-written one.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb
import pandas as pd

from spark_core.ids import slug

TABLES = ("DIM_SECTIONS", "DIM_STUDENTS", "FACT_ENROLLMENTS", "DIM_ASSIGNMENTS", "STUDENT_PERFORMANCE")

SECTION_MEMBERSHIP_VIEW = """
CREATE OR REPLACE VIEW V_SECTION_MEMBERSHIP AS
SELECT * EXCLUDE(rn) FROM (
  SELECT COURSE_ID, ENROLLMENT_ID, SECTION_ID, STUDENT_ID, ENROLLMENT_TYPE, ENROLLMENT_STATE,
         UPDATED_AT, CURRENT_SCORE, CURRENT_GRADE, CURRENT_POINTS,
         ROW_NUMBER() OVER (PARTITION BY COURSE_ID, SECTION_ID, STUDENT_ID
                            ORDER BY UPDATED_AT DESC NULLS LAST, ENROLLMENT_ID DESC NULLS LAST) AS rn
  FROM FACT_ENROLLMENTS
  WHERE ENROLLMENT_TYPE ILIKE '%Student%'
) t WHERE rn = 1;
"""


def course_db_path(data_dir: Path, course_id: str) -> Path:
    return Path(data_dir) / "courses" / f"{slug(course_id)}.duckdb"


def strip_timezone(df: pd.DataFrame) -> pd.DataFrame:
    """Store timestamps as naive UTC (same convention as the Streamlit app)."""
    df = df.copy()
    for col in df.select_dtypes(include=["datetimetz"]).columns:
        df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return df


def write_course_db(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Build a complete course DB in a temp file, then atomically swap it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".duckdb.tmp")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        for name, df in tables.items():
            frame = strip_timezone(df)
            con.register("frame", frame)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
            con.unregister("frame")
        if "FACT_ENROLLMENTS" in tables:
            con.execute(SECTION_MEMBERSHIP_VIEW)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, path)


@contextmanager
def read_course_db(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    if not Path(path).exists():
        raise FileNotFoundError(f"course database not built yet: {path.name}")
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()


def query_df(path: Path, sql: str, params: list | None = None) -> pd.DataFrame:
    with read_course_db(path) as con:
        return con.execute(sql, params or []).fetchdf()
