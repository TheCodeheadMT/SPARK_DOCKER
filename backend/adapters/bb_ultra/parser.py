"""
bb_ultra_parser.py -- Blackboard Ultra export parser for SPARK (prototype).

Turns two Ultra exports into the same raw frames the Canvas extractors produce,
so the existing transform_data() / build_features() pipeline runs unchanged:

    Full Gradebook  (.csv, or Ultra's tab-delimited UTF-16 .xls)
    Grade History   (.csv / .xls)

Outputs (see parse_course):
    sections     COURSE_ID, SECTION_ID, SECTION_NAME                    (DIM_SECTIONS)
    enrollments  COURSE_ID, SECTION_ID, STUDENT_ID, ... CURRENT_SCORE   (FACT_ENROLLMENTS)
    students     STUDENT_ID, STUDENT_NAME, STUDENT_SORTABLE_NAME, ...   (DIM_STUDENTS)
    assignments  ASSIGNMENT_ID, ASSIGNMENT_NAME, DUE_AT, POINTS_POSSIBLE, ASSIGNMENT_GROUP_ID (+ DUE_SOURCE)
    submissions  STUDENT_ID, ASSIGNMENT_ID, POINTS_EARNED, SUBMITTED_AT, GRADED_AT, LATE

Decisions encoded here (from the first real export, Nov 2026):
  * STUDENT_ID = Bb username (present in both files). Student ID kept as SIS_USER_ID.
  * SECTION_ID = "Child Course ID" (merged sections).
  * ASSIGNMENT_ID = the "|710001" token in the gradebook header. Grade History has
    no ID, so it is joined by column name.
  * Calculated columns ("Total Pts: up to ...", or display type other than Score)
    are excluded from assignments; the configured total column feeds CURRENT_SCORE.
  * SUBMITTED_AT = latest "Attempt Activity" for that student + item (student submissions).
    Items graded by hand (e.g. in-class quizzes) have no submission time -> NaT.
  * DUE_AT is inferred from Ultra's automatic-zero burst: system edits (empty role) with
    value 0 right after an item closes. Rounded back to 23:59 of the prior day when the
    burst lands just after midnight. A manifest can override (DUE_SOURCE records which).
  * "Needs Grading" / blank cells are excluded from submissions (not counted as zeros).
    Items nobody has a score for yet are treated as not yet active and skipped.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd

HEADER_RE = re.compile(
    r"^(?P<name>.*?)\s*\[Total Pts:\s*(?P<upto>up to\s+)?(?P<pts>[\d.]+)\s+(?P<disp>[^\]]+)\]\s*\|(?P<id>\d+)\s*$"
)
HISTORY_DATE_FMT = "%b %d, %Y %I:%M:%S %p"
ROLE_STUDENT, ROLE_INSTRUCTOR, ROLE_SYSTEM = "S", "P", ""
NON_SCORE_VALUES = {"needs grading", "in progress", "exempt", "-", ""}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def read_bb_table(path: str | Path) -> pd.DataFrame:
    """Read a Bb export: UTF-16 tab-delimited '.xls' or a UTF-8 csv. All values as str."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    first = text.splitlines()[0] if text else ""
    delim = "\t" if first.count("\t") > first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header, body = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    body = [r + [""] * (len(header) - len(r)) for r in body]
    return pd.DataFrame(body, columns=[h.strip() for h in header], dtype="string")


# ---------------------------------------------------------------------------
# Gradebook
# ---------------------------------------------------------------------------
TOTAL_NAME_HINTS = ("final", "overall", "total", "weighted", "course grade", "current grade")


def _pick_total(gb: pd.DataFrame, cols: pd.DataFrame, configured: str | None) -> tuple[str | None, str]:
    """The gradebook column to use as the current grade, and where it came from.

    A column is only used if most students have a numeric value in it, so a column that
    displays letters, or is empty, falls through to the next choice.
    """
    def usable(h: str) -> bool:
        vals = gb[h].map(_num)
        return bool(len(vals)) and vals.notna().mean() >= 0.5 and vals.dropna().between(0, 200).mean() >= 0.95
    if configured and usable(configured):
        return configured, "configured_column"
    calc = cols[cols["CALCULATED"] & cols["DISPLAY"].str.lower().str.contains("percent")]
    for hint in TOTAL_NAME_HINTS:
        for h, name in zip(calc["HEADER"], calc["ASSIGNMENT_NAME"]):
            if hint in name.lower() and usable(h):
                return h, "percentage_column"
    return None, "computed_points"

def parse_gradebook_columns(columns: list[str]) -> pd.DataFrame:
    out = []
    for c in columns:
        m = HEADER_RE.match(c)
        if not m:
            continue
        disp = m["disp"].strip()
        out.append({
            "HEADER": c,
            "ASSIGNMENT_ID": f"bb:{m['id']}",
            "ASSIGNMENT_NAME": m["name"].strip(),
            "POINTS_POSSIBLE": float(m["pts"]),
            "DISPLAY": disp,
            "CALCULATED": bool(m["upto"]) or disp.lower() != "score",
        })
    return pd.DataFrame(out)


def _num(v) -> float | None:
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    if s.lower() in NON_SCORE_VALUES:
        return None
    s = s.replace(",", "").removesuffix("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Grade history
# ---------------------------------------------------------------------------
def parse_grade_history(df: pd.DataFrame) -> pd.DataFrame:
    h = df.copy()
    for col in ("Date", "Attempt Activity"):
        h[col] = pd.to_datetime(h[col], format=HISTORY_DATE_FMT, errors="coerce")
    h["ROLE"] = h["Last Edited by: Role"].fillna("").str.strip()
    h["VALUE_NUM"] = pd.to_numeric(h["Value"], errors="coerce")
    return h


def infer_due_dates(hist: pd.DataFrame, min_events: int = 1) -> pd.DataFrame:
    """Due date per column from the automatic-zero burst (system role, value 0)."""
    z = hist[(hist["ROLE"] == ROLE_SYSTEM) & (hist["VALUE_NUM"] == 0)]
    g = z.groupby("Column")["Date"].agg(["min", "count"]).reset_index()
    g = g[g["count"] >= min_events]

    def snap(ts: pd.Timestamp) -> pd.Timestamp:
        # Burst just after midnight -> item closed at 23:59 the previous day.
        if ts.hour == 0 and ts.minute < 15:
            return (ts.normalize() - timedelta(minutes=1))
        # Burst at 23:5x -> item closed at 23:59 that day (job ran slightly early).
        if ts.hour == 23 and ts.minute >= 50:
            return ts.normalize() + timedelta(hours=23, minutes=59)
        return ts.floor("min")

    g["DUE_AT"] = g["min"].map(snap)
    return g.rename(columns={"Column": "ASSIGNMENT_NAME", "count": "AUTO_ZERO_EVENTS"})[
        ["ASSIGNMENT_NAME", "DUE_AT", "AUTO_ZERO_EVENTS"]
    ]


# ---------------------------------------------------------------------------
# Course assembly
# ---------------------------------------------------------------------------
def parse_course(
    gradebook_path: str | Path,
    history_path: str | Path | None,
    course_id: str,
    total_column: str = "Final Grade",
    letter_column: str = "Overall Grade",
    manifest: pd.DataFrame | None = None,      # optional: ASSIGNMENT_NAME, DUE_AT
    late_grace_minutes: int = 0,
) -> dict[str, pd.DataFrame]:
    gb = read_bb_table(gradebook_path)
    # Merged course: everyone who is in a section has a Child Course ID. Rows without one are enrolled only in
    # the parent course (typically instructors, TAs, preview/test students or people being moved) and are
    # left out of every dataset. A course with no Child Course IDs at all is treated as one section.
    excluded_no_section = 0
    if "Child Course ID" in gb.columns:
        has_sec = gb["Child Course ID"].fillna("").str.strip() != ""
        if has_sec.any():
            excluded_no_section = int((~has_sec).sum())
            gb = gb[has_sec].reset_index(drop=True)
    cols = parse_gradebook_columns(list(gb.columns))
    items = cols[~cols["CALCULATED"]].copy()

    # --- students / sections / enrollments -------------------------------
    students = pd.DataFrame({
        "STUDENT_ID": gb["Username"].str.strip(),
        "STUDENT_NAME": (gb["First Name"].str.strip() + " " + gb["Last Name"].str.strip()),
        "STUDENT_SORTABLE_NAME": (gb["Last Name"].str.strip() + ", " + gb["First Name"].str.strip()),
        "LOGIN_ID": gb["Username"].str.strip(),
        "SIS_USER_ID": gb.get("Student ID", pd.Series(dtype="string")).str.strip(),
    }).drop_duplicates("STUDENT_ID")

    section_col = "Child Course ID" if "Child Course ID" in gb.columns else None
    sec_vals = gb[section_col].fillna("").str.strip() if section_col else pd.Series([""] * len(gb), dtype="string")
    single = bool((sec_vals == "").all())
    sec_vals = sec_vals.where(sec_vals != "", "ALL")
    sections = pd.DataFrame({"SECTION_ID": sorted(sec_vals.unique())})
    sections.insert(0, "COURSE_ID", course_id)
    sections["SECTION_NAME"] = ("All students" if single
                                else sections["SECTION_ID"].str.rsplit("-", n=1).str[-1])

    def header_for(name: str) -> str | None:
        hits = cols.loc[cols["ASSIGNMENT_NAME"] == name, "HEADER"]
        return hits.iat[0] if len(hits) else None

    total_h, total_source = _pick_total(gb, cols, header_for(total_column))
    letter_h = header_for(letter_column)
    if letter_h is None:
        letters = cols[cols["CALCULATED"] & cols["DISPLAY"].str.lower().str.contains("letter")]
        letter_h = letters["HEADER"].iat[0] if len(letters) else None
    enrollments = pd.DataFrame({
        "COURSE_ID": course_id,
        "ENROLLMENT_ID": course_id + ":" + gb["Username"].str.strip() + ":" + sec_vals,
        "SECTION_ID": sec_vals,
        "STUDENT_ID": gb["Username"].str.strip(),
        "ENROLLMENT_TYPE": "StudentEnrollment",
        "ENROLLMENT_STATE": gb.get("Availability", pd.Series(["Yes"] * len(gb))).map(
            lambda v: "active" if str(v).strip().lower() == "yes" else "inactive"),
        "UPDATED_AT": pd.to_datetime(gb.get("Last Access"), errors="coerce"),
        "CURRENT_SCORE": gb[total_h].map(_num) if total_h else None,     # filled from points below if None
        "CURRENT_GRADE": gb[letter_h] if letter_h else None,
        "CURRENT_POINTS": None,
        "STUDENT_NAME": gb["First Name"].str.strip() + " " + gb["Last Name"].str.strip(),
        "STUDENT_SORTABLE_NAME": gb["Last Name"].str.strip() + ", " + gb["First Name"].str.strip(),
        "LOGIN_ID": gb["Username"].str.strip(),
        "SIS_USER_ID": gb.get("Student ID", pd.Series([None] * len(gb))),
    })
    enrollments["CURRENT_SCORE"] = pd.to_numeric(enrollments["CURRENT_SCORE"], errors="coerce")

    # Letter-only grade columns ("B+") are not scores.
    if letter_h is not None and enrollments["CURRENT_GRADE"].notna().any():
        enrollments["CURRENT_GRADE"] = enrollments["CURRENT_GRADE"].where(enrollments["CURRENT_GRADE"].str.strip() != "")

    # --- scores (long) ----------------------------------------------------
    long = gb.melt(id_vars=["Username"], value_vars=list(items["HEADER"]), var_name="HEADER", value_name="RAW")
    long["Username"] = long["Username"].str.strip()
    long = long.merge(items[["HEADER", "ASSIGNMENT_ID", "ASSIGNMENT_NAME"]], on="HEADER")
    long["POINTS_EARNED"] = long["RAW"].map(_num)
    long["STATUS"] = long["RAW"].fillna("").str.strip().str.lower().map(
        lambda s: "needs_grading" if s == "needs grading" else ("blank" if s in ("", "-") else "graded"))

    # Items nobody has a numeric score for yet are not active -> skip.
    active_ids = set(long.loc[long["POINTS_EARNED"].notna(), "ASSIGNMENT_ID"])
    items["ACTIVE"] = items["ASSIGNMENT_ID"].isin(active_ids)

    # --- grade history: submissions, graded, category, due ----------------
    hist = parse_grade_history(read_bb_table(history_path)) if history_path else None
    if hist is not None:
        stu_sub = hist[(hist["ROLE"] == ROLE_STUDENT) & hist["Attempt Activity"].notna()]
        submitted = (stu_sub.groupby(["Username", "Column"])["Attempt Activity"].max()
                     .rename("SUBMITTED_AT").reset_index())
        graded = (hist[hist["ROLE"] != ROLE_STUDENT].groupby(["Username", "Column"])["Date"].max()
                  .rename("GRADED_AT").reset_index())
        category = (hist.groupby("Column")["Grade Category"].agg(lambda s: s.mode().iat[0])
                    .rename("ASSIGNMENT_GROUP_ID").reset_index())
        due = infer_due_dates(hist)
        due["DUE_SOURCE"] = "inferred_auto_zero"
    else:
        submitted = pd.DataFrame(columns=["Username", "Column", "SUBMITTED_AT"])
        graded = pd.DataFrame(columns=["Username", "Column", "GRADED_AT"])
        category = pd.DataFrame(columns=["Column", "ASSIGNMENT_GROUP_ID"])
        due = pd.DataFrame(columns=["ASSIGNMENT_NAME", "DUE_AT", "AUTO_ZERO_EVENTS", "DUE_SOURCE"])

    if manifest is not None and not manifest.empty:
        m = manifest[["ASSIGNMENT_NAME", "DUE_AT"]].copy()
        m["DUE_AT"] = pd.to_datetime(m["DUE_AT"], errors="coerce")
        m["DUE_SOURCE"] = "manifest"
        due = pd.concat([m, due[~due["ASSIGNMENT_NAME"].isin(m["ASSIGNMENT_NAME"])]], ignore_index=True)

    items = (items.merge(due, on="ASSIGNMENT_NAME", how="left")
                  .merge(category.rename(columns={"Column": "ASSIGNMENT_NAME"}), on="ASSIGNMENT_NAME", how="left"))
    items["ASSIGNMENT_GROUP_ID"] = items["ASSIGNMENT_GROUP_ID"].fillna("No Category")
    items["DUE_SOURCE"] = items["DUE_SOURCE"].fillna("none")

    assignments = items[items["ACTIVE"]][
        ["ASSIGNMENT_ID", "ASSIGNMENT_NAME", "DUE_AT", "POINTS_POSSIBLE", "ASSIGNMENT_GROUP_ID", "DUE_SOURCE"]
    ].reset_index(drop=True)

    subs = long[long["STATUS"] == "graded"].merge(
        submitted, left_on=["Username", "ASSIGNMENT_NAME"], right_on=["Username", "Column"], how="left"
    ).drop(columns=["Column"]).merge(
        graded, left_on=["Username", "ASSIGNMENT_NAME"], right_on=["Username", "Column"], how="left"
    ).drop(columns=["Column"]).merge(assignments[["ASSIGNMENT_ID", "DUE_AT"]], on="ASSIGNMENT_ID", how="inner")

    grace = pd.Timedelta(minutes=late_grace_minutes)
    subs["LATE"] = (subs["SUBMITTED_AT"].notna() & subs["DUE_AT"].notna()
                    & (subs["SUBMITTED_AT"] > subs["DUE_AT"] + grace))
    submissions = subs.rename(columns={"Username": "STUDENT_ID"})[
        ["STUDENT_ID", "ASSIGNMENT_ID", "POINTS_EARNED", "SUBMITTED_AT", "GRADED_AT", "LATE"]
    ].reset_index(drop=True)

    pending = long[long["STATUS"] == "needs_grading"][["Username", "ASSIGNMENT_NAME"]]

    if total_h is None:
        # No usable total column: points earned / points possible over graded items (matched Blackboard's
        # Final Grade exactly in the reference course; differs if the course uses category weights).
        pts = submissions.merge(assignments[["ASSIGNMENT_ID", "POINTS_POSSIBLE"]], on="ASSIGNMENT_ID")
        tot = pts.groupby("STUDENT_ID")[["POINTS_EARNED", "POINTS_POSSIBLE"]].sum()
        pct = (tot["POINTS_EARNED"] / tot["POINTS_POSSIBLE"].where(tot["POINTS_POSSIBLE"] > 0) * 100).round(2)
        enrollments["CURRENT_SCORE"] = enrollments["STUDENT_ID"].map(pct)
        total_source = "computed_points"

    return {
        "sections": sections,
        "enrollments": enrollments,
        "students": students,
        "assignments": assignments,
        "submissions": submissions,
        "items_report": items,          # every gradable item incl. inactive, for the validation report
        "meta": {"excluded_no_section": excluded_no_section, "current_grade_source": total_source,
                 "current_grade_column": cols.loc[cols["HEADER"] == total_h, "ASSIGNMENT_NAME"].iat[0] if total_h else None},
        "pending_grading": pending,
        "history": hist,
    }


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------
def validation_report_dict(parsed: dict[str, pd.DataFrame]) -> dict:
    st, a, s, it = parsed["students"], parsed["assignments"], parsed["submissions"], parsed["items_report"]
    hist = parsed["history"]
    rep = {
        "students": int(len(st)),
        "sections": list(parsed["sections"]["SECTION_NAME"]),
        "items_total": int(len(it)),
        "items_active": int(it["ACTIVE"].sum()),
        "items_not_yet_active": list(it.loc[~it["ACTIVE"], "ASSIGNMENT_NAME"]),
        "due_dates_inferred": int((a["DUE_SOURCE"] == "inferred_auto_zero").sum()),
        "due_dates_manifest": int((a["DUE_SOURCE"] == "manifest").sum()),
        "due_dates_missing": list(a.loc[a["DUE_SOURCE"] == "none", "ASSIGNMENT_NAME"]),
        "graded_cells": int(len(s)),
        "with_submit_time": int(s["SUBMITTED_AT"].notna().sum()),
        "late": int(s["LATE"].sum()),
        "needs_grading_excluded": int(len(parsed["pending_grading"])),
        "history_loaded": hist is not None,
        "history_columns_not_in_gradebook": [],
        "history_users_not_in_gradebook": 0,
        **parsed.get("meta", {}),
    }
    if hist is not None:
        rep["history_columns_not_in_gradebook"] = sorted(set(hist["Column"].dropna()) - set(it["ASSIGNMENT_NAME"]))
        # Count only (no identifiers) so the report itself never carries student IDs.
        rep["history_users_not_in_gradebook"] = len(set(hist["Username"].dropna()) - set(st["STUDENT_ID"]))
    return rep


def validation_report(parsed: dict[str, pd.DataFrame]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in validation_report_dict(parsed).items())
