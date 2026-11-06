"""EI service: assemble a student's signals and build redacted prompts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from spark_api.services import queries
from spark_core.ei import prompts as P
from spark_core.ei import signals as S
from spark_core.ei.redact import redact
from spark_core.etl.store import query_df

METHODOLOGY_FILE = Path(__file__).resolve().parents[2] / "spark_core" / "ei" / "methodology.md"


@lru_cache
def methodology() -> str:
    return METHODOLOGY_FILE.read_text(encoding="utf-8")


def snapshot_as_of(db_file: Path) -> pd.Timestamp | None:
    """Latest activity anywhere in the course: the moment the export describes."""
    r = query_df(db_file, "SELECT GREATEST(MAX(SUBMITTED_AT), MAX(GRADED_AT)) AS t FROM STUDENT_PERFORMANCE")
    t = r["t"].iat[0] if len(r) else None
    return None if t is None or pd.isna(t) else pd.Timestamp(t)


def identity(db_file: Path, student_id: str) -> dict:
    cols = query_df(db_file, "SELECT * FROM DIM_STUDENTS WHERE STUDENT_ID = ?", [student_id])
    if cols.empty:
        return {"STUDENT_ID": student_id}
    return {k: (None if pd.isna(v) else str(v)) for k, v in cols.iloc[0].to_dict().items()}


@dataclass
class StudentState:
    student_id: str
    ident: dict
    rows: pd.DataFrame
    signals: dict
    activity: float | None
    risk: str | None
    as_of: pd.Timestamp | None

    @property
    def name(self) -> str:
        return self.ident.get("STUDENT_NAME") or self.student_id


def load_state(db_file: Path, student_id: str) -> StudentState | None:
    ident = identity(db_file, student_id)
    if "STUDENT_NAME" not in ident:
        return None
    rows = queries.student_rows(db_file, student_id)
    as_of = snapshot_as_of(db_file)
    sig = S.compute_signals(rows, as_of.to_pydatetime() if as_of is not None else None)
    return StudentState(student_id, ident, rows, sig, S.compute_activity_index(sig), queries.risk_for(db_file, student_id),
                        as_of)


def triage(st: StudentState) -> dict:
    flag, reasons = S.needs_intervention(st.signals, st.risk, st.activity)
    return {"needs_intervention": flag, "reasons": reasons}


def _scrub(st: StudentState, text: str) -> tuple[str, int]:
    ids = [st.ident.get(k) for k in ("STUDENT_ID", "LOGIN_ID", "SIS_USER_ID")]
    return redact(text or "", st.ident.get("STUDENT_NAME"), ids + [st.ident.get("STUDENT_SORTABLE_NAME")])


def profile(st: StudentState, course_name: str, objectives: str, instructor_notes: str,
            student_notes: str) -> tuple[P.Profile, int]:
    total = 0
    clean = []
    for t in (objectives, instructor_notes, student_notes):
        c, n = _scrub(st, t)
        clean.append(c.strip())
        total += n
    perf, n = _scrub(st, S.summarize_performance(st.rows))   # assignment titles could, rarely, carry a name
    total += n
    return P.Profile(course_name=course_name, learning_objectives=clean[0] or "None provided",
                     performance_summary=perf, instructor_notes=clean[1], student_notes=clean[2]), total


def build_prompt(st: StudentState, kind: str, course_name: str, objectives: str, instructor_notes: str,
                 student_notes: str, cfg: P.WorkbookConfig | None = None,
                 modules: list[dict] | None = None) -> dict:
    prof, n = profile(st, course_name, objectives, instructor_notes, student_notes)
    if kind == "plan":
        system, user = P.lesson_plan_prompt(prof)
        modules = None
    elif kind == "objectives":
        system, user = P.objectives_prompt(prof)
        modules = None
    elif kind == "workbook":
        modules = modules or S.recommend_modules(st.signals, st.risk, prof.instructor_notes, prof.student_notes)
        sig_text = S.format_signals_for_prompt(st.signals, st.activity)
        system, user = P.workbook_prompt(prof, sig_text, modules, cfg or P.WorkbookConfig())
    else:
        raise ValueError(f"unknown prompt kind {kind!r}")
    return {"kind": kind, "system": system, "user": user, "redactions": n, "modules": modules}


def prompt_leaks(st: StudentState, text: str) -> list[str]:
    """Identifiers still present after redaction (used by tests and as a last check before sending)."""
    out = []
    for k in ("STUDENT_NAME", "LOGIN_ID", "SIS_USER_ID", "STUDENT_ID"):
        v = st.ident.get(k)
        if v and len(v) >= 3 and re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", text, flags=re.IGNORECASE):
            out.append(k)
    return out
