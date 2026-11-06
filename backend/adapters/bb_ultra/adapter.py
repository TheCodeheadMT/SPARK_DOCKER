"""Blackboard Ultra adapter: reads exports from the WebDAV-mounted Content Collection
and/or from exports instructors upload to SPARK (DATA_DIR/inbox).

Both locations use the same layout under their root (e.g. /mnt/bb or DATA_DIR/inbox):

    SPARK/
      <parent_course_id>/
        gradebook/      Full Gradebook exports (.csv or Ultra .xls)   newest file wins
        grade_history/  Grade History exports                         newest file wins
        manifest/       assignments.csv (optional due-date overrides)

The mount is treated as read-only, slow and unreliable:

  * fingerprint() uses only directory listings and stat() (name, size, mtime), so a
    poll never downloads anything.
  * Each export is copied ONCE to a local snapshot directory and hashed locally. An
    index keyed by (name, size, mtime) means later checks reuse that copy instead of
    reading the file over WebDAV again.
  * If a file's size or mtime changes while it is being copied, the copy is discarded
    and ExportInProgress is raised (Blackboard may still be writing it).
  * Old snapshots are pruned (KEEP_SNAPSHOTS per input).

When a course has exports in both places, the newest file of each kind wins.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from adapters.base import CourseData, CourseRef
from adapters.bb_ultra.parser import parse_course, validation_report_dict
from spark_core.ids import make_course_id, slug

EXPORT_SUFFIXES = {".csv", ".xls", ".txt"}
KEEP_SNAPSHOTS = 5
INPUT_KEYS = ("gradebook", "grade_history", "manifest")
# Folders that are never courses (davfs2 creates lost+found in the mount for its backups).
NOT_COURSES = {"lost+found"}

# errno values a broken davfs2 mount typically produces.
MOUNT_ERRNOS = {errno.ENOTCONN, errno.EIO, errno.ESTALE, errno.EHOSTDOWN, errno.ETIMEDOUT, errno.ECONNABORTED}


class ExportInProgress(RuntimeError):
    """The export changed while it was being read; try again on the next poll."""


def is_mount_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in MOUNT_ERRNOS


def _exports(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")
            and p.suffix.lower() in EXPORT_SUFFIXES]


def _newest(*folders: Path) -> Path | None:
    files = [f for d in folders for f in _exports(d)]
    return max(files, key=lambda p: (p.stat().st_mtime, p.name)) if files else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stat_key(p: Path) -> tuple[str, dict]:
    st = p.stat()
    return f"{p.name}|{st.st_size}|{st.st_mtime_ns}", {"name": p.name, "size": st.st_size, "mtime": st.st_mtime}


class BlackboardFileAdapter:
    lms = "bb"

    def __init__(self, bb_root: Path | None, snapshot_dir: Path, total_column: str = "Final Grade",
                 letter_column: str = "Overall Grade", late_grace_minutes: int = 0, inbox_root: Path | None = None):
        self.root = Path(bb_root) / "SPARK" if bb_root else None             # the WebDAV mount
        self.inbox = Path(inbox_root) / "SPARK" if inbox_root else None      # uploaded exports
        self.roots = [r for r in (self.root, self.inbox) if r is not None]
        self.snapshot_dir = Path(snapshot_dir)
        self.total_column = total_column
        self.letter_column = letter_column
        self.late_grace_minutes = late_grace_minutes

    # -- discovery -----------------------------------------------------------
    def list_courses(self) -> list[CourseRef]:
        names: set[str] = set()
        for root in self.roots:
            if root.is_dir():
                names |= {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                          and p.name not in NOT_COURSES}
        return [CourseRef(course_id=make_course_id("bb", n), name=n, lms="bb", source=n) for n in sorted(names)]

    @staticmethod
    def folder_name(course: CourseRef) -> str:
        # source is the course folder name (older rows stored a full path; its last part is the name)
        return Path(course.source).name if course.source else course.course_id.split(":", 1)[1]

    def _dirs(self, course: CourseRef) -> list[Path]:
        return [root / self.folder_name(course) for root in self.roots]

    def _inputs(self, course: CourseRef) -> dict[str, Path | None]:
        dirs = self._dirs(course)
        manifests = [m for m in (d / "manifest" / "assignments.csv" for d in dirs) if m.is_file()]
        return {
            "gradebook": _newest(*(d / "gradebook" for d in dirs)),
            "grade_history": _newest(*(d / "grade_history" for d in dirs)),
            "manifest": max(manifests, key=lambda m: m.stat().st_mtime) if manifests else None,
        }

    def fingerprint(self, course: CourseRef) -> dict:
        """Cheap change detector: listings + stat only, nothing is downloaded."""
        files: dict[str, dict | None] = {}
        for key, p in self._inputs(course).items():
            files[key] = _stat_key(p)[1] if p else None
        newest = max((f["mtime"] for f in files.values() if f), default=None)
        token = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
        return {"token": token, "files": files, "newest_mtime": newest, "has_gradebook": files["gradebook"] is not None}

    # -- snapshots -----------------------------------------------------------
    def _course_snap_dir(self, course: CourseRef) -> Path:
        d = self.snapshot_dir / slug(course.course_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _load_index(d: Path) -> dict:
        try:
            return json.loads((d / "index.json").read_text())
        except (FileNotFoundError, ValueError):
            return {}

    @staticmethod
    def _save_index(d: Path, index: dict) -> None:
        tmp = d / "index.json.tmp"
        tmp.write_text(json.dumps(index, indent=1))
        os.replace(tmp, d / "index.json")

    def _snapshot_one(self, d: Path, index: dict, key: str, p: Path) -> tuple[Path, str]:
        skey, _ = _stat_key(p)
        hit = index.get(f"{key}:{skey}")
        if hit and (d / hit["file"]).exists():
            return d / hit["file"], hit["sha256"]
        fd, tmp_name = tempfile.mkstemp(dir=d, prefix=f".{key}_", suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            shutil.copyfile(p, tmp)                          # the only read over WebDAV
            if _stat_key(p)[0] != skey:
                raise ExportInProgress(f"{p.name} changed while it was being copied")
            digest = _sha256(tmp)
            target = d / f"{key}_{digest[:12]}{p.suffix.lower()}"
            if target.exists():
                tmp.unlink()
            else:
                os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        index[f"{key}:{skey}"] = {"file": target.name, "sha256": digest}
        return target, digest

    def _prune(self, d: Path, index: dict) -> None:
        keep: set[str] = set()
        for key in INPUT_KEYS:
            files = sorted(d.glob(f"{key}_*"), key=lambda f: f.stat().st_mtime, reverse=True)
            keep |= {f.name for f in files[:KEEP_SNAPSHOTS]}
            for f in files[KEEP_SNAPSHOTS:]:
                f.unlink(missing_ok=True)
        for k in [k for k, v in index.items() if v["file"] not in keep]:
            del index[k]

    def snapshot(self, course: CourseRef) -> tuple[dict[str, Path | None], dict[str, Path | None], str]:
        """Copy current inputs locally (reusing earlier copies). Returns (mount paths, local paths, version)."""
        inputs = self._inputs(course)
        d = self._course_snap_dir(course)
        index = self._load_index(d)
        local: dict[str, Path | None] = {}
        h = hashlib.sha256()
        for key in sorted(inputs):
            p = inputs[key]
            h.update(key.encode())
            if p is None:
                local[key] = None
                h.update(b"-")
                continue
            local[key], digest = self._snapshot_one(d, index, key, p)
            h.update(digest.encode())
        self._prune(d, index)
        self._save_index(d, index)
        return inputs, local, h.hexdigest()[:16]

    def source_version(self, course: CourseRef) -> str:
        return self.snapshot(course)[2]

    # -- load ----------------------------------------------------------------
    def load_course(self, course: CourseRef) -> CourseData:
        inputs, snap, version = self.snapshot(course)
        if snap["gradebook"] is None:
            raise FileNotFoundError(f"No Full Gradebook export found for {course.name}. Upload one, or save it to "
                                    "the course's gradebook folder in Blackboard.")
        manifest = pd.read_csv(snap["manifest"]) if snap["manifest"] else None
        p = parse_course(
            snap["gradebook"], snap["grade_history"], course_id=course.course_id,
            total_column=self.total_column, letter_column=self.letter_column,
            manifest=manifest, late_grace_minutes=self.late_grace_minutes,
        )
        report = validation_report_dict(p)
        report["files"] = {k: (v.name if v else None) for k, v in inputs.items()}
        return CourseData(
            sections=p["sections"], enrollments=p["enrollments"], students=p["students"],
            assignments=p["assignments"], submissions=p["submissions"],
            source_version=version, report=report,
        )
