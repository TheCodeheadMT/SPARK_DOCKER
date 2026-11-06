"""Admissions Excel loader, ported from tabs/data_tab.py.

Kept unchanged: header-row detection, column normalisation, the canonical
DIM_ADMISSIONS schema with defaults, every field mapping and label clean-up,
FL_NAME_KEY (first+last) keys, append-only inserts keyed by FL_NAME_KEY, the
file-hash ingest log (DIM_ADMISSIONS_FILE_LOG) and .zip support. The resulting
admissions.duckdb is compatible with the Streamlit app's file.

Changed:
  * No Streamlit; functions take bytes and return result dicts.
  * Writes are atomic: copy the DB to a temp file, insert, then os.replace(), so the
    Analytics page (reading read-only) never sees a half-written file.
  * Uploaded workbooks are not kept on disk (the Streamlit app saved a copy of every
    upload under admissions_uploads/); only the SHA-256 is logged.
  * Sport code mapping tables (DIM_ADMISSIONS_SPORT_MAP_*) are not rebuilt; nothing reads them.
  * SEX and RACE are no longer read or stored (data minimisation; SPARK never used them).
    Existing databases from the Streamlit app are scrubbed of those columns on the next
    upload, or immediately via scrub_removed_columns(). Note: if the old Streamlit app
    opens the scrubbed file, its schema check re-adds the columns, filled with defaults only.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

ADMISSIONS_TABLE = "DIM_ADMISSIONS"
REMOVED_COLUMNS = ("SEX", "RACE")
FILE_LOG_TABLE = "DIM_ADMISSIONS_FILE_LOG"
_write_lock = threading.Lock()

ADMISSIONS_SCHEMA: list[tuple[str, str, object]] = [
    ("ID", "VARCHAR", ""), ("NAME_KEY_FL", "VARCHAR", ""), ("FL_NAME_KEY", "VARCHAR", ""),
    ("SCHOLAR", "VARCHAR", "None"), ("ACADEMICALLY_CHALLENGED", "VARCHAR", "None"),
    ("INTERNATIONAL", "VARCHAR", "No"), ("CMPAC", "INTEGER", pd.NA), ("ACT_ENGL", "INTEGER", pd.NA),
    ("ACT_MATH", "INTEGER", pd.NA), ("SAT_MATH", "INTEGER", pd.NA), ("SAT_VERB", "INTEGER", pd.NA),
    ("PRIM_SPORT", "VARCHAR", "None"), ("SEC_SPORT", "VARCHAR", "None"), ("USAF_PREP", "VARCHAR", "None"),
    ("EXTERNAL_PREP", "VARCHAR", "None"), ("CONSIDERED_HONORS", "VARCHAR", "No"),
    ("PROG_EXPERIENCE", "VARCHAR", "None"), ("CYBER_PATRIOT", "VARCHAR", "No"), ("JAVA", "VARCHAR", "Not Applicable"),
    ("PYTHON", "VARCHAR", "Not Applicable"), ("C_CPP", "VARCHAR", "Not Applicable"),
    ("OTHER_PROG", "VARCHAR", "Not Applicable"),
]
SCHEMA_COLS = [c for c, _, _ in ADMISSIONS_SCHEMA]
DEFAULTS = {c: d for c, _, d in ADMISSIONS_SCHEMA}

PROG_LABEL_MAP = {
    "Two semesters (full year) of programming coursework": "Two Semesters (full year)",
    "Two semesters": "Two Semesters (full year)", "Two semesters (full year)": "Two Semesters (full year)",
    "One semester (half year) of programming coursework": "One Semester", "One semester": "One Semester",
    "More than two semesters of programming coursework": "More than Two Semesters",
    "More than two semesters": "More than Two Semesters", "None": "None", "Not Applicable": "Not Applicable",
    "Self-taught or learned programming for a job": "Self-taught (job/other)",
}


# ---------------------------------------------------------------- helpers (unchanged logic)
def _safe_colname(col: Any) -> str:
    col = re.sub(r"[^0-9a-zA-Z]+", "_", str(col or "").strip())
    col = re.sub(r"_+", "_", col).strip("_")
    return col.upper() if col else "COL"


def _pick_col(df: pd.DataFrame, *candidates: str) -> str | None:
    for c in candidates:
        cc = _safe_colname(c)
        if cc in df.columns:
            return cc
    return None


def _clean_str(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().replace({"nan": ""})


def _value_or(s: pd.Series, default: str = "None") -> pd.Series:
    x = _clean_str(s)
    return x.where(x.ne(""), default)


def _yes_no(s: pd.Series, default_no: str = "No") -> pd.Series:
    yes = _clean_str(s).str.upper().isin(["Y", "YES", "TRUE", "T", "1"])
    return pd.Series(np.where(yes, "Yes", default_no), index=s.index)


def standardize_prog_labels(s: pd.Series, default: str = "None") -> pd.Series:
    x = _clean_str(s)
    x = x.where(x.ne(""), default)
    return x.map(PROG_LABEL_MAP).fillna(x)


def _normalize_name_part(x: Any) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return re.sub(r"\s+", " ", str(x).strip().replace(".", " "))


def _name_key_fl(first: Any, last: Any) -> tuple[str, str]:
    parts = [p for p in (_normalize_name_part(first), _normalize_name_part(last)) if p]
    spaced = " ".join(parts).strip().lower()
    return spaced, re.sub(r"[^a-z0-9]+", "", spaced)


def _find_header_row(xlsx: bytes) -> int:
    try:
        preview = pd.read_excel(io.BytesIO(xlsx), sheet_name=0, header=None, nrows=10)
    except Exception:
        return 1
    for i in range(min(len(preview), 10)):
        vals = [str(v).strip().lower() for v in preview.iloc[i].tolist() if v is not None and str(v).strip() != "nan"]
        if "first name" in vals and "last" in vals:
            return i
    return 1


def coerce_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=SCHEMA_COLS)
    df = df.copy()
    for col in SCHEMA_COLS:
        if col not in df.columns:
            df[col] = DEFAULTS[col]
    for col, t, default in ADMISSIONS_SCHEMA:
        if t.startswith("INT"):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        else:
            df[col] = df[col].astype("string").fillna("").str.strip().replace({"": str(default)}).astype(str)
    return df[SCHEMA_COLS].copy()


def load_admissions_excel(xlsx: bytes) -> pd.DataFrame:
    """AdmissionsData.xlsx bytes -> DataFrame in the canonical DIM_ADMISSIONS schema."""
    df = pd.read_excel(io.BytesIO(xlsx), sheet_name=0, header=_find_header_row(xlsx))
    df.columns = [_safe_colname(c) for c in df.columns]
    df = df.drop(columns=[c for c in (_pick_col(df, "SEQUENCE"), _pick_col(df, "Status 4=International")) if c],
                 errors="ignore")
    first_col, last_col = _pick_col(df, "FIRST_NAME", "First Name"), _pick_col(df, "LAST", "Last")
    out = pd.DataFrame(index=df.index)
    if first_col and last_col:
        keys = df.apply(lambda r: _name_key_fl(r.get(first_col), r.get(last_col)), axis=1, result_type="expand")
        out["NAME_KEY_FL"], out["FL_NAME_KEY"] = keys[0], keys[1]
    if "ID" in df.columns:
        out["ID"] = pd.to_numeric(df["ID"], errors="coerce")

    def put(target, fn, *names, **kw):
        c = _pick_col(df, *names)
        if c:
            out[target] = fn(df[c], **kw)

    put("SCHOLAR", _value_or, "Academy Scholar", default="None")
    put("ACADEMICALLY_CHALLENGED", _value_or, "4/5/1", default="None")
    put("INTERNATIONAL", _yes_no, "Internatio Flag", "International Flag", "International")
    for target, names in {"CMPAC": ("CMPAC Score", "CMPAC"), "ACT_ENGL": ("ACT_ENGL", "ACT ENGL", "ACT_ENGLISH", "ACT ENGLISH"),
                          "ACT_MATH": ("ACT_MATH", "ACT MATH"), "SAT_MATH": ("SAT_MATH", "SAT MATH"),
                          "SAT_VERB": ("SAT_VERB", "SAT VERB", "SAT_VERBAL", "SAT VERBAL")}.items():
        put(target, lambda s: pd.to_numeric(s, errors="coerce"), *names)
    put("PRIM_SPORT", _value_or, "Primary Sport", "Sport", default="None")
    put("SEC_SPORT", _value_or, "Sport 2", "Sport2", "Secondary Sport", default="None")
    put("USAF_PREP", _value_or, "PREP_SCHOOL AF", default="None")
    put("EXTERNAL_PREP", _value_or, "PREP_SCHOOL EXT", default="None")
    put("CONSIDERED_HONORS", _yes_no, "Considered for Honors CS110?")
    put("PROG_EXPERIENCE", standardize_prog_labels, "Best Describe Progrm Exp:", default="None")
    put("CYBER_PATRIOT", _yes_no, "Cyper Patriot or other comps?", "Cyber Patriot or other comps?",
        "Cyber Patriot or Other Comp?")
    put("JAVA", standardize_prog_labels, "Java Program Experience", "Java?", default="Not Applicable")
    put("PYTHON", standardize_prog_labels, "Python Experience", "Python?", default="Not Applicable")
    put("C_CPP", standardize_prog_labels, "C or C++ Experience", "C or C++?", default="Not Applicable")
    put("OTHER_PROG", standardize_prog_labels, "Experience Other Program Lang?", "Other Programming Language?",
        default="Not Applicable")
    return coerce_to_schema(out)


# ---------------------------------------------------------------- DB writes (atomic)
def _existing_columns(con: duckdb.DuckDBPyConnection) -> set[str]:
    try:
        return {str(c).upper() for c in con.execute(f"PRAGMA table_info('{ADMISSIONS_TABLE}')").fetchdf()["name"]}
    except duckdb.Error:
        return set()


def _drop_removed(con: duckdb.DuckDBPyConnection) -> list[str]:
    dropped = [c for c in REMOVED_COLUMNS if c in _existing_columns(con)]
    for c in dropped:
        con.execute(f"ALTER TABLE {ADMISSIONS_TABLE} DROP COLUMN {c}")
    return dropped


def _ensure_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    cols_sql = ", ".join(f"{c} {t}" for c, t, _ in ADMISSIONS_SCHEMA)
    con.execute(f"CREATE TABLE IF NOT EXISTS {ADMISSIONS_TABLE} ({cols_sql})")
    dropped = _drop_removed(con)
    # Older/partial schemas: add any missing canonical column with its default.
    have = _existing_columns(con)
    for col, t, default in ADMISSIONS_SCHEMA:
        if col not in have:
            dflt = "NULL" if t.startswith("INT") else "'" + str(default).replace("'", "''") + "'"
            con.execute(f"ALTER TABLE {ADMISSIONS_TABLE} ADD COLUMN {col} {t} DEFAULT {dflt}")
    con.execute(f"""CREATE TABLE IF NOT EXISTS {FILE_LOG_TABLE} (FILE_NAME VARCHAR, FILE_HASH VARCHAR,
        ADDED_AT TIMESTAMP, ROWS_IN_FILE INTEGER, NEW_ROWS_INSERTED INTEGER, TOTAL_ROWS_AFTER INTEGER)""")
    return dropped


def _xlsx_members(name: str, data: bytes) -> list[tuple[str, bytes]]:
    if not name.lower().endswith(".zip"):
        return [(name, data)]
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xlsx = [n for n in z.namelist() if n.lower().endswith(".xlsx") and not n.startswith("__MACOSX")]
        preferred = [n for n in xlsx if n.split("/")[-1].lower() == "admissionsdata.xlsx"]
        for m in preferred or xlsx:
            out.append((f"{Path(name).name}:{m}", z.read(m)))
    return out


def ingest_admissions(db_path: Path, file_name: str, data: bytes, dry_run: bool = False) -> list[dict]:
    """Append new students from an .xlsx (or a .zip of them). Returns one result per workbook."""
    db_path = Path(db_path)
    members = _xlsx_members(file_name, data)
    if not members:
        raise ValueError("No .xlsx files found.")
    with _write_lock:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.with_suffix(".duckdb.tmp")
        if tmp.exists():
            tmp.unlink()
        if db_path.exists():
            shutil.copy2(db_path, tmp)
        con = duckdb.connect(str(tmp))
        results = []
        dropped: list[str] = []
        try:
            dropped = _ensure_tables(con)
            for mname, mbytes in members:
                h = hashlib.sha256(mbytes).hexdigest()
                total = lambda: int(con.execute(f"SELECT COUNT(*) FROM {ADMISSIONS_TABLE}").fetchall()[0][0])
                if con.execute(f"SELECT COUNT(*) FROM {FILE_LOG_TABLE} WHERE FILE_HASH = ?", [h]).fetchall()[0][0]:
                    results.append({"file_name": mname, "status": "skipped (already ingested)", "rows_in_file": 0,
                                    "new_rows": 0, "total_after": total()})
                    continue
                df = load_admissions_excel(mbytes)
                df = df[df["FL_NAME_KEY"].astype(str).str.strip().ne("")].drop_duplicates("FL_NAME_KEY")
                con.register("incoming", df)
                new = int(con.execute(f"""SELECT COUNT(*) FROM incoming i WHERE NOT EXISTS
                    (SELECT 1 FROM {ADMISSIONS_TABLE} d WHERE d.FL_NAME_KEY = i.FL_NAME_KEY)""").fetchall()[0][0])
                cols = ", ".join(SCHEMA_COLS)
                con.execute(f"""INSERT INTO {ADMISSIONS_TABLE} ({cols}) SELECT {cols} FROM incoming i WHERE NOT EXISTS
                    (SELECT 1 FROM {ADMISSIONS_TABLE} d WHERE d.FL_NAME_KEY = i.FL_NAME_KEY)""")
                con.unregister("incoming")
                after = total()
                con.execute(f"INSERT INTO {FILE_LOG_TABLE} VALUES (?, ?, NOW(), ?, ?, ?)", [mname, h, len(df), new, after])
                results.append({"file_name": mname, "status": "would ingest" if dry_run else "ingested",
                                "rows_in_file": int(len(df)), "new_rows": new, "total_after": after})
            con.execute("CHECKPOINT")
        finally:
            con.close()
        if dry_run:
            tmp.unlink(missing_ok=True)
        elif dropped:
            _compact_into(tmp, db_path)
        else:
            os.replace(tmp, db_path)
        return results


def _compact_into(src: Path, dest: Path) -> None:
    """Copy every table from src into a brand-new file and atomically replace dest.

    Dropping a column does not reliably erase its old values from the DuckDB file; a
    fresh file contains only what is still in the tables.
    """
    fresh = dest.with_suffix(".duckdb.fresh")
    fresh.unlink(missing_ok=True)
    con = duckdb.connect(str(fresh))
    try:
        con.execute(f"ATTACH '{src}' AS old (READ_ONLY)")
        for (t,) in con.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'old'").fetchall():
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM old.{t}")
        con.execute("DETACH old")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    Path(src).unlink(missing_ok=True)
    os.replace(fresh, dest)


def scrub_removed_columns(db_path: Path) -> list[str]:
    """Drop SEX/RACE from an existing admissions DB (atomic). Returns the columns removed."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with _write_lock:
        tmp = db_path.with_suffix(".duckdb.tmp")
        tmp.unlink(missing_ok=True)
        shutil.copy2(db_path, tmp)
        con = duckdb.connect(str(tmp))
        try:
            dropped = _drop_removed(con)
            con.execute("CHECKPOINT")
        finally:
            con.close()
        if not dropped:
            tmp.unlink(missing_ok=True)
            return []
        _compact_into(tmp, db_path)
        return dropped


def admissions_status(db_path: Path) -> dict:
    if not Path(db_path).exists():
        return {"exists": False, "students": 0, "files": 0, "last_ingest": None, "history": []}
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        n = con.execute(f"SELECT COUNT(*) FROM {ADMISSIONS_TABLE}").fetchall()[0][0]
        legacy = sorted(c for c in REMOVED_COLUMNS if c in _existing_columns(con))
        hist = con.execute(f"""SELECT FILE_NAME, ADDED_AT, ROWS_IN_FILE, NEW_ROWS_INSERTED, TOTAL_ROWS_AFTER
                              FROM {FILE_LOG_TABLE} ORDER BY ADDED_AT DESC LIMIT 25""").fetchdf()
    except duckdb.Error:
        return {"exists": True, "students": 0, "files": 0, "last_ingest": None, "history": []}
    finally:
        con.close()
    return {"exists": True, "students": int(n), "files": int(len(hist)), "legacy_sensitive_columns": legacy,
            "last_ingest": hist["ADDED_AT"].max().isoformat() if len(hist) else None,
            "history": [{"file_name": r.FILE_NAME, "added_at": r.ADDED_AT.isoformat(), "rows_in_file": int(r.ROWS_IN_FILE),
                         "new_rows": int(r.NEW_ROWS_INSERTED), "total_after": int(r.TOTAL_ROWS_AFTER)}
                        for r in hist.itertuples()]}
