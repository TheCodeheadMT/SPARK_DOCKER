"""Blackboard exports uploaded by instructors.

Uploaded files are written into DATA_DIR/inbox/SPARK/<course>/{gradebook,grade_history}/,
the same layout as the WebDAV mount, so ingest, snapshots and the newest-file-wins rule
work the same way for both. Nothing is saved until the upload has been checked:

  * each file is identified as a Full Gradebook or a Grade History from its columns
    (the instructor doesn't have to say which is which);
  * the files are parsed together exactly as an ingest would, so a broken export is
    rejected before it is stored;
  * likely mistakes are flagged: a gradebook from a different course (by its
    Child Course IDs or by how few students match the course's current data), or a
    grade history whose students don't match the gradebook.

Files are stored under SPARK-generated names; the original file name is only reported.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from adapters.bb_ultra.parser import HEADER_RE, parse_course, read_bb_table

KINDS = ("gradebook", "grade_history")
ALLOWED_SUFFIXES = (".csv", ".xls", ".txt")
KEEP_UPLOADS = 5
COURSE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,99}$")
GRADEBOOK_COLS = {"Last Name", "First Name", "Username"}
HISTORY_COLS = {"Date", "Column", "Username", "Value"}
LOW_OVERLAP = 0.5


class UploadError(ValueError):
    pass


@dataclass
class InspectedFile:
    filename: str
    kind: str | None
    path: Path                     # temporary copy of the upload on local disk
    suffix: str
    rows: int = 0
    students: list[str] = field(default_factory=list, repr=False)
    items: int = 0
    sections: list[str] = field(default_factory=list)
    suggested_code: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    error: str | None = None

    def summary(self) -> dict:
        return {"filename": self.filename, "kind": self.kind, "rows": self.rows, "students": len(self.students),
                "items": self.items, "sections": self.sections, "suggested_course_code": self.suggested_code,
                "first_date": self.first_date, "last_date": self.last_date, "error": self.error}


def _suggest_code(sections: list[str]) -> str | None:
    """Child Course IDs look like <parent>-<section>; the shared parent is the course code."""
    parents = {s.rsplit("-", 1)[0] for s in sections if "-" in s}
    return parents.pop() if len(parents) == 1 else None


def inspect(filename: str, path: Path) -> InspectedFile:
    suffix = Path(filename).suffix.lower()
    f = InspectedFile(filename=filename, kind=None, path=Path(path), suffix=suffix)
    if suffix not in ALLOWED_SUFFIXES:
        f.error = "Not a Blackboard export (expected .csv or .xls)."
        return f
    try:
        df = read_bb_table(path)
    except Exception:  # noqa: BLE001
        f.error = "Could not read this file as a Blackboard export."
        return f
    cols = set(df.columns)
    f.rows = len(df)
    if GRADEBOOK_COLS <= cols and any(HEADER_RE.match(c) for c in df.columns):
        f.kind = "gradebook"
        f.items = sum(1 for c in df.columns if HEADER_RE.match(c))
        if "Child Course ID" in cols:
            sec = df["Child Course ID"].fillna("").str.strip()
            f.sections = sorted(s for s in sec.unique() if s)
            f.suggested_code = _suggest_code(f.sections)
            if f.sections:                       # people with no section are left out at ingest, so here too
                df = df[sec != ""]
        f.students = sorted(df["Username"].dropna().str.strip().unique())
    elif HISTORY_COLS <= cols:
        f.kind = "grade_history"
        f.students = sorted(df["Username"].dropna().str.strip().unique())
        f.items = df["Column"].nunique()
        dates = pd.to_datetime(df["Date"], format="%b %d, %Y %I:%M:%S %p", errors="coerce").dropna()
        if len(dates):
            f.first_date, f.last_date = dates.min().isoformat(), dates.max().isoformat()
    else:
        f.error = ("This doesn't look like a Full Gradebook or Grade History export. In the Blackboard gradebook "
                   "use Download, then Full Gradebook (all items) or Grade History.")
    return f


@dataclass
class Check:
    ok: bool
    files: list[InspectedFile]
    errors: list[str]
    warnings: list[str]
    suggested_code: str | None

    def dict(self) -> dict:
        return {"ok": self.ok, "files": [f.summary() for f in self.files], "errors": self.errors,
                "warnings": self.warnings, "suggested_course_code": self.suggested_code}


def check(files: list[InspectedFile], course_code: str | None, current_students: set[str] | None,
          has_current_gradebook: bool, current_gradebook: Path | None = None,
          has_current_history: bool = False) -> Check:
    errors = [f"{f.filename}: {f.error}" for f in files if f.error]
    warnings: list[str] = []
    by_kind: dict[str, InspectedFile] = {}
    for f in files:
        if f.kind:
            if f.kind in by_kind:
                errors.append(f"Two {f.kind.replace('_', ' ')} files were uploaded "
                              f"({by_kind[f.kind].filename}, {f.filename}). Upload one of each.")
            by_kind[f.kind] = f
    if not files:
        errors.append("No files were uploaded.")
    gb, hist = by_kind.get("gradebook"), by_kind.get("grade_history")
    if not gb and not has_current_gradebook and not errors:
        errors.append("The first upload for a course must include the Full Gradebook export.")
    suggested = gb.suggested_code if gb else None

    if not errors:
        if gb and course_code and suggested and suggested.lower() != course_code.lower():
            warnings.append(f"This gradebook's sections belong to {suggested}, not {course_code}. "
                            "Check that you downloaded it from the right course.")
        if gb and current_students:
            overlap = len(current_students & set(gb.students)) / max(len(current_students), 1)
            if overlap < LOW_OVERLAP:
                warnings.append(f"Only {overlap:.0%} of the students already in SPARK for this course are in "
                                "this gradebook. It may be from a different course.")
        if gb and hist:
            overlap = len(set(hist.students) & set(gb.students)) / max(len(set(hist.students)), 1)
            if overlap < LOW_OVERLAP:
                warnings.append(f"Only {overlap:.0%} of the students in the grade history are in the gradebook. "
                                "Check that both files are from the same course.")
        if gb and not hist and not has_current_history:
            warnings.append("No Grade History uploaded: SPARK can't see submission times, so late work and "
                            "due dates will be missing until you upload one.")
        # Parse everything together exactly as an ingest would.
        try:
            _trial_parse(gb, hist, current_gradebook)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"The export could not be processed: {type(exc).__name__}: {exc}")
    return Check(ok=not errors, files=files, errors=errors, warnings=warnings, suggested_code=suggested)


def _trial_parse(gb: InspectedFile | None, hist: InspectedFile | None, current_gradebook: Path | None) -> None:
    gb_path = gb.path if gb else current_gradebook
    if gb_path is None:
        return
    parse_course(gb_path, hist.path if hist else None, course_id="bb:check")


def save(inbox_spark: Path, course_code: str, files: list[InspectedFile]) -> dict[str, str]:
    """Write checked files into the inbox and prune old uploads. Returns kind -> stored name."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = {}
    for f in files:
        if f.kind not in KINDS:
            continue
        d = inbox_spark / course_code / f.kind
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"{f.kind}_{stamp}{f.suffix}"
        tmp = d / f".{target.name}.part"
        shutil.copyfile(f.path, tmp)
        os.replace(tmp, target)
        out[f.kind] = target.name
        old = sorted((p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        for p in old[KEEP_UPLOADS:]:
            p.unlink(missing_ok=True)
    return out
