"""Admissions enrichment, ported from etl/transform.py.

Reads the shared admissions DuckDB (DATA_DIR/admissions.duckdb, table DIM_ADMISSIONS)
read-only and left-joins it to cluster rows by FL_NAME_KEY. The admissions upload
screen comes with the Data & Models port; until then an existing admissions.duckdb
from the Streamlit app can be copied into DATA_DIR.

Name-key rules are unchanged from the original, including the institution-specific
cleanup (leading letter + two digits, "afacademyafedu").
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

ADMISSIONS_COLS = [
    "FL_NAME_KEY", "CMPAC", "ACT_ENGL", "ACT_MATH", "SAT_MATH", "SAT_VERB",
    "SCHOLAR", "ACADEMICALLY_CHALLENGED", "INTERNATIONAL", "USAF_PREP", "CYBER_PATRIOT", "CONSIDERED_HONORS",
    "PROG_EXPERIENCE", "PYTHON", "JAVA", "C_CPP", "OTHER_PROG", "PRIM_SPORT", "SEC_SPORT",
]
NUMERIC_FIELDS = {"CMPAC": "CMPAC", "ACT_ENGL": "ACT English", "ACT_MATH": "ACT Math", "SAT_MATH": "SAT Math",
                  "SAT_VERB": "SAT English"}
YESNO_FIELDS = {"INTERNATIONAL": "International", "CYBER_PATRIOT": "Cyber Patriot/Other",
                "CONSIDERED_HONORS": "Considered Honors"}
CATEGORY_FIELDS = {"SCHOLAR": "Scholar", "ACADEMICALLY_CHALLENGED": "Academically Challenged",
                   "USAF_PREP": "USAF Prep", "PROG_EXPERIENCE": "Programming Experience",
                   "PYTHON": "Python Experience", "JAVA": "Java Experience", "C_CPP": "C or C++ Experience",
                   "OTHER_PROG": "Other Programming Experience", "PRIM_SPORT": "Primary Sport",
                   "SEC_SPORT": "Secondary Sport"}


def make_fl_name_key(name: str | None) -> str:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = re.sub(r"\s+", " ", str(name).strip().replace(".", " "))
    if "," in s:
        last, rest = [p.strip() for p in s.split(",", 1)]
        parts = [p for p in (rest + " " + last).split(" ") if p]
    else:
        parts = [p for p in s.split(" ") if p]
    compact = re.sub(r"[^a-z0-9]+", "", " ".join(parts).lower())
    compact = re.sub(r"^[a-z]\d{2}", "", compact)
    return compact.replace("afacademyafedu", "")


def enrich_with_admissions(clusters: pd.DataFrame, admissions_db: Path) -> tuple[pd.DataFrame, float]:
    """Return (clusters + admissions columns, match rate). Missing DB -> unchanged, 0.0."""
    df = clusters.copy()
    if not Path(admissions_db).exists():
        return df, 0.0
    df["FL_NAME_KEY"] = df["STUDENT_NAME"].map(make_fl_name_key)
    con = duckdb.connect(str(admissions_db), read_only=True)
    try:
        existing = set(con.execute("PRAGMA table_info('DIM_ADMISSIONS')").fetchdf()["name"].astype(str))
        cols = [c for c in ADMISSIONS_COLS if c in existing]
        adm = con.execute(f"SELECT {', '.join(cols)} FROM DIM_ADMISSIONS").fetchdf() if "FL_NAME_KEY" in cols \
            else pd.DataFrame(columns=["FL_NAME_KEY"])
    except duckdb.Error:
        return df, 0.0
    finally:
        con.close()
    adm = adm.drop_duplicates("FL_NAME_KEY")
    out = df.merge(adm, on="FL_NAME_KEY", how="left")
    num = [c for c in NUMERIC_FIELDS if c in out]
    rate = float(out[num].notna().any(axis=1).mean()) if num else 0.0
    return out, rate
