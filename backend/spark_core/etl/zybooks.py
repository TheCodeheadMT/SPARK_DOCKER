"""zyBooks roster report ingest, ported from tabs/data_tab.py + performance_tab.py.

Kept: the 3-row header parse (Category / Chapter / Metric), which cells are kept
("Completion" and "Time spent" categories), the '%' / '-' value clean-up, and the
per-student pivot (Participation, zyLab, Minutes) used by the charts.

Changed:
  * Stored PER COURSE (DATA_DIR/zybooks/<course>.duckdb). The Streamlit version dropped
    and recreated one global table on every upload, so uploading course B erased course A.
  * Students are matched to the course roster at upload time and the match is stored
    (STUDENT_ID), so pages never re-match by name. Order: email == login/username,
    email local-part == username, then first+last name.
  * Uploaded files are not kept; atomic write (temp + os.replace) like every other DB.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd


def parse_report(data: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(data), header=[0, 1, 2])
    find = lambda pat: next((c for c in df.columns if pat in str(c).lower()), None)
    email_col, first_col, last_col = find("email"), find("first name"), find("last name")
    if email_col is None:
        raise ValueError("Could not find an 'Email' column in the zyBooks report.")
    recs = []
    for _, row in df.iterrows():
        raw = row[email_col]
        if pd.isna(raw):
            continue
        email = str(raw).strip().lower()
        fn = str(row[first_col]).strip() if first_col is not None else ""
        ln = str(row[last_col]).strip() if last_col is not None else ""
        for col in df.columns:
            l0, l1, l2 = [str(c).strip() for c in col]
            if "Completion" in l0 or "Time spent" in l0:
                try:
                    clean = str(row[col]).replace("%", "").strip()
                    val = float(clean) if clean and clean not in ("-", "nan") else 0.0
                except ValueError:
                    val = 0.0
                recs.append({"EMAIL": email, "FIRST_NAME": fn, "LAST_NAME": ln, "CATEGORY": l0,
                             "CHAPTER": l1, "METRIC": l2, "VALUE": val})
    if not recs:
        raise ValueError("No completion or time-spent columns found in the zyBooks report.")
    return pd.DataFrame(recs)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def match_students(activity: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    """roster: STUDENT_ID, LOGIN_ID, STUDENT_NAME. Adds STUDENT_ID + MATCHED_BY to activity."""
    by_login = {str(r.LOGIN_ID).strip().lower(): r.STUDENT_ID for r in roster.itertuples() if pd.notna(r.LOGIN_ID)}
    by_user = {_norm(r.LOGIN_ID): r.STUDENT_ID for r in roster.itertuples() if pd.notna(r.LOGIN_ID)}
    by_name = {}
    for r in roster.itertuples():
        parts = str(r.STUDENT_NAME or "").split()
        if len(parts) >= 2:
            by_name[_norm(parts[0]) + "|" + _norm(parts[-1])] = r.STUDENT_ID
    people = activity[["EMAIL", "FIRST_NAME", "LAST_NAME"]].drop_duplicates("EMAIL")
    rows = []
    for p in people.itertuples():
        sid, how = by_login.get(p.EMAIL), "email"
        if sid is None:
            sid, how = by_user.get(_norm(p.EMAIL.split("@")[0])), "username"
        if sid is None:
            sid, how = by_name.get(_norm(p.FIRST_NAME) + "|" + _norm(p.LAST_NAME)), "name"
        rows.append({"EMAIL": p.EMAIL, "STUDENT_ID": sid, "MATCHED_BY": how if sid is not None else None})
    return activity.merge(pd.DataFrame(rows), on="EMAIL", how="left")


def write_course_zybooks(db_path: Path, activity: pd.DataFrame, file_name: str) -> dict:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".duckdb.tmp")
    tmp.unlink(missing_ok=True)
    act = activity.copy()
    act["UPLOADED_AT"] = datetime.now(timezone.utc).replace(tzinfo=None)
    con = duckdb.connect(str(tmp))
    try:
        con.register("act", act)
        con.execute("CREATE TABLE FACT_ZYBOOKS_ACTIVITY AS SELECT * FROM act")
        con.execute("CREATE TABLE ZYBOOKS_FILE_LOG (FILE_NAME VARCHAR, UPLOADED_AT TIMESTAMP)")
        con.execute("INSERT INTO ZYBOOKS_FILE_LOG VALUES (?, ?)", [file_name, act["UPLOADED_AT"].iat[0]])
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(tmp, db_path)
    people = act.drop_duplicates("EMAIL")
    return {"records": int(len(act)), "people": int(len(people)),
            "matched": int(people["STUDENT_ID"].notna().sum()),
            "matched_by": {k: int(v) for k, v in people["MATCHED_BY"].value_counts().items()},
            "chapters": int(act["CHAPTER"].nunique())}


def _read(db_path: Path, sql: str, params: list | None = None) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql, params or []).fetchdf()
    finally:
        con.close()


def _chapter_key(s) -> float:
    try:
        return float(str(s).split(".")[0])
    except ValueError:
        return 999.0


def pivot(df: pd.DataFrame, index: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    d["TYPE"] = d["METRIC"]
    d.loc[d["CATEGORY"].str.contains("Time spent", case=False, na=False), "TYPE"] = "Minutes"
    d = d[d["TYPE"].isin(["Participation", "zyLab", "Minutes"])]
    if d.empty:
        return d
    p = d.pivot_table(index=index, columns="TYPE", values="VALUE", aggfunc="mean").reset_index()
    for c in ("Participation", "zyLab", "Minutes"):
        if c not in p:
            p[c] = 0.0
    p[["Participation", "zyLab", "Minutes"]] = p[["Participation", "zyLab", "Minutes"]].fillna(0)
    p["SortOrder"] = p["CHAPTER"].map(_chapter_key)
    return p.sort_values(["SortOrder", "CHAPTER"]).reset_index(drop=True)


def student_detail(db_path: Path, student_id: str) -> pd.DataFrame:
    return pivot(_read(db_path, "SELECT CHAPTER, CATEGORY, METRIC, VALUE FROM FACT_ZYBOOKS_ACTIVITY WHERE STUDENT_ID = ?",
                       [student_id]), ["CHAPTER"])


def course_matrix(db_path: Path) -> pd.DataFrame:
    return pivot(_read(db_path, "SELECT STUDENT_ID, CHAPTER, CATEGORY, METRIC, VALUE FROM FACT_ZYBOOKS_ACTIVITY "
                                "WHERE STUDENT_ID IS NOT NULL"), ["STUDENT_ID", "CHAPTER"])


def status(db_path: Path) -> dict:
    log = _read(db_path, "SELECT FILE_NAME, UPLOADED_AT FROM ZYBOOKS_FILE_LOG")
    if log.empty:
        return {"exists": False}
    people = _read(db_path, "SELECT COUNT(DISTINCT EMAIL) AS p, COUNT(DISTINCT STUDENT_ID) AS m, "
                            "COUNT(DISTINCT CHAPTER) AS c FROM FACT_ZYBOOKS_ACTIVITY")
    return {"exists": True, "file_name": log["FILE_NAME"].iat[0], "uploaded_at": log["UPLOADED_AT"].iat[0].isoformat(),
            "people": int(people["p"].iat[0]), "matched": int(people["m"].iat[0]), "chapters": int(people["c"].iat[0])}
