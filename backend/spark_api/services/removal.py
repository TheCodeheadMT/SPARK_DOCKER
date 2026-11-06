"""Removing courses from SPARK.

Two levels:
  * hide   | "Remove from my courses": the course leaves this user's list and their access
             is suspended until they restore it. Nothing is deleted; others are unaffected.
  * delete | "Delete from SPARK": the course, its data and everything derived from it are
             removed for everyone. Allowed for admins, or for the only person with access.
             Blocked while the course folder still exists on the Blackboard mount, because the
             next poll would bring it straight back.

The audit log is never deleted: who viewed which student stays on record.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete, func, or_, select

from spark_api.db import (Course, CourseAccess, CoursePref, Database, EISession, HiddenCourse, IngestRun, ModelAlias,
                          SourceWatch, SuccessModel)
from spark_core.etl.store import course_db_path, query_df
from spark_core.ids import slug


class RemovalError(Exception):
    pass


def course_paths(data_dir: Path, course: Course, inbox_spark: Path | None) -> dict[str, Path]:
    s = slug(course.id)
    paths = {
        "course_db": course_db_path(data_dir, course.id),
        "snapshots": data_dir / "snapshots" / s,
        "models": data_dir / "models" / s,
        "zybooks": data_dir / "zybooks" / f"{s}.duckdb",
    }
    if course.lms == "bb" and inbox_spark is not None:
        paths["uploads"] = inbox_spark / (Path(course.source).name if course.source else course.id.split(":", 1)[1])
    return paths


def on_mount(adapter, course: Course) -> bool:
    """True if the course folder exists on the Blackboard mount (it would be rediscovered)."""
    if course.lms != "bb" or adapter is None or adapter.root is None:
        return False
    try:
        return (adapter.root / (Path(course.source).name if course.source else course.id.split(":", 1)[1])).is_dir()
    except OSError:
        return True            # mount unreadable: be safe, assume it's there


def impact(db: Database, data_dir: Path, course: Course, upn: str, is_admin: bool, adapter,
           mount_check=None) -> dict:
    with db.session() as s:
        others = s.scalar(select(func.count()).select_from(CourseAccess)
                          .where(CourseAccess.course_id == course.id, CourseAccess.upn != upn)) or 0
        mine = s.scalars(select(CourseAccess).where(CourseAccess.course_id == course.id,
                                                    CourseAccess.upn == upn)).first()
        runs = s.scalar(select(func.count()).select_from(IngestRun).where(IngestRun.course_id == course.id)) or 0
        models = s.scalar(select(func.count()).select_from(SuccessModel)
                          .where(SuccessModel.course_id == course.id)) or 0
        ei = s.scalar(select(func.count()).select_from(EISession).where(EISession.course_id == course.id)) or 0
        reusers = s.execute(select(Course.id, Course.name).join(ModelAlias, ModelAlias.course_id == Course.id)
                            .where(ModelAlias.source_course_id == course.id)).all()
    f = course_db_path(data_dir, course.id)
    students = None
    if f.exists():
        try:
            students = int(query_df(f, "SELECT COUNT(*) AS n FROM DIM_STUDENTS")["n"].iat[0])
        except Exception:  # noqa: BLE001
            students = None
    mounted = mount_check(course) if mount_check else on_mount(adapter, course)

    can_hide = mine is not None and not is_admin
    blocked = None
    if not is_admin and mine is None:
        blocked = "You don't have access to this course."
    elif not is_admin and mine.role not in ("Instructor", "CourseDirector"):
        blocked = "Only an instructor, course director or administrator can delete a course."
    elif not is_admin and others:
        blocked = (f"{others} other {'person has' if others == 1 else 'people have'} access to this course, so only "
                   "an administrator can delete it. You can still remove it from your own list.")
    elif course.lms == "fixture":
        blocked = ("Demo courses are recreated every time SPARK starts. To remove them, an administrator sets "
                   "ENABLE_FIXTURES=false. You can remove it from your own list instead.")
    elif course.lms == "canvas":
        blocked = ("Canvas courses are re-added by course discovery while SPARK is connected to Canvas. "
                   "You can remove it from your own list instead.")
    elif mounted:
        blocked = ("This course's folder is still in the Blackboard SPARK folder, so it would come back on the next "
                   "check. Remove the folder in Blackboard first, then delete it here.")
    return {
        "course": {"id": course.id, "name": course.name, "lms": course.lms,
                   "code": course.id.split(":", 1)[1]},
        "can_hide": can_hide,
        "can_delete": blocked is None,
        "delete_blocked_reason": blocked,
        "others_with_access": others,
        "impact": {"students": students, "ingest_runs": runs, "success_models": models, "ei_sessions": ei,
                   "models_reused_by": [{"id": i, "name": n} for i, n in reusers]},
    }


def hide(db: Database, upn: str, course_id: str) -> None:
    with db.session() as s:
        if s.get(HiddenCourse, (upn, course_id)) is None:
            s.add(HiddenCourse(upn=upn, course_id=course_id))
        s.commit()


def restore(db: Database, upn: str, course_id: str) -> bool:
    with db.session() as s:
        row = s.get(HiddenCourse, (upn, course_id))
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def delete_course(db: Database, data_dir: Path, course: Course, inbox_spark: Path | None) -> list[str]:
    """Delete metadata rows (explicitly: SQLite doesn't enforce ON DELETE CASCADE by default) and files."""
    cid = course.id
    with db.session() as s:
        for model in (HiddenCourse, CoursePref, CourseAccess, IngestRun, SuccessModel, EISession, SourceWatch):
            s.execute(delete(model).where(model.course_id == cid))
        s.execute(delete(ModelAlias).where(or_(ModelAlias.course_id == cid, ModelAlias.source_course_id == cid)))
        s.execute(delete(Course).where(Course.id == cid))
        s.commit()
    removed = []
    for name, p in course_paths(data_dir, course, inbox_spark).items():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
        elif p.exists():
            p.unlink(missing_ok=True)
            removed.append(name)
        for extra in (p.with_name(p.name + ".wal"), p.with_name(p.name + ".tmp")):
            extra.unlink(missing_ok=True)
    return removed
