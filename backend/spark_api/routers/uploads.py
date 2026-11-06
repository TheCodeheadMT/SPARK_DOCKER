"""Upload Blackboard exports to SPARK (no WebDAV or Blackboard admin needed).

Both endpoints take the files plus dry_run. With dry_run=true (the default) nothing is
stored: the response says what each file is and lists any errors or warnings. With
dry_run=false the files are saved and the course is ingested straight away.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from spark_api.auth.base import User
from spark_api.db import AuditEvent, Course, CourseAccess, Database
from adapters.base import CourseRef
from spark_api.deps import CourseContext, current_user, get_db, require_course_role
from spark_api.services import uploads as U
from spark_api.services.ingest import IngestBusy, run_ingest
from spark_core.etl.store import course_db_path, query_df
from spark_core.ids import make_course_id

router = APIRouter(prefix="/api")
MAX_FILES = 4
CHUNK = 1 << 20


def _adapter(request: Request):
    ad = request.app.state.adapters.get("bb")
    if not request.app.state.settings.uploads_enabled or ad is None or ad.inbox is None:
        raise HTTPException(status_code=404, detail="Uploading exports is turned off on this server.")
    return ad


async def _spool(f: UploadFile, tmpdir: Path, limit_mb: int) -> Path:
    """Copy an upload to local disk in 1 MB chunks, stopping as soon as it passes the size limit."""
    limit = limit_mb * 1024 * 1024
    target = tmpdir / f"{len(list(tmpdir.iterdir()))}{Path(f.filename or 'upload.csv').suffix.lower()[:8]}"
    size = 0
    with open(target, "wb") as out:
        while chunk := await f.read(CHUNK):
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"{f.filename}: file too large ({limit_mb} MB max)")
            out.write(chunk)
    return target


async def _inspect(request: Request, files: list[UploadFile], tmpdir: Path) -> list[U.InspectedFile]:
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Upload the Full Gradebook and the Grade History (2 files).")
    limit_mb = request.app.state.settings.upload_max_mb
    out = []
    for f in files:
        path = await _spool(f, tmpdir, limit_mb)
        out.append(await run_in_threadpool(U.inspect, f.filename or "upload.csv", path))
    return out


@contextmanager
def _workdir(request: Request):
    """Per-request scratch folder on the data disk (not /tmp, which may be small); always removed."""
    root = request.app.state.settings.data_dir / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=root, prefix="upload-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _audit(db: Database, upn: str, course_id: str, target: str) -> None:
    with db.session() as s:
        s.add(AuditEvent(upn=upn, action="upload_exports", course_id=course_id, target=target))
        s.commit()


def _run_summary(run) -> dict:
    return {"id": run.id, "status": run.status, "error": run.error, "report": run.report or {}}


async def _commit(request: Request, db: Database, ad, course: Course, code: str, files: list[U.InspectedFile],
                  upn: str) -> dict:
    stored = await run_in_threadpool(U.save, ad.inbox, code, files)
    _audit(db, upn, course.id, ",".join(f"{k}={v}" for k, v in stored.items()))
    try:
        run = await run_in_threadpool(run_ingest, db, ad, course, request.app.state.settings.data_dir, upn, False)
    except IngestBusy:
        return {"stored": stored, "run": None, "note": "Saved. An ingest was already running; it will be "
                                                      "picked up by the next ingest."}
    return {"stored": stored, "run": _run_summary(run)}


# ---------------------------------------------------------------- existing course
@router.post("/courses/{course_id}/uploads")
async def upload_to_course(course_id: str, request: Request, files: list[UploadFile] = File(...),
                           dry_run: bool = Form(True),
                           ctx: CourseContext = Depends(require_course_role("Instructor")),
                           db: Database = Depends(get_db)):
    ad = _adapter(request)
    if ctx.course.lms != "bb":
        raise HTTPException(status_code=400, detail="Exports can only be uploaded to Blackboard courses.")
    with _workdir(request) as tmpdir:
        return await _upload_to_course(request, ctx, db, ad, files, dry_run, tmpdir)


async def _upload_to_course(request, ctx, db, ad, files, dry_run, tmpdir):
    course_id = ctx.course.id
    ref = CourseRef(course_id, ctx.course.name, "bb", ctx.course.source)
    code = ad.folder_name(ref)
    inspected = await _inspect(request, files, tmpdir)
    f = course_db_path(request.app.state.settings.data_dir, course_id)
    current = set(query_df(f, "SELECT STUDENT_ID FROM DIM_STUDENTS")["STUDENT_ID"]) if f.exists() else None
    poller = request.app.state.poller          # the mount may hang: read it under the poller's timeout
    try:
        inputs = await run_in_threadpool(poller.io.call, ad._inputs, ref) if poller else ad._inputs(ref)
        current_gb, current_hist = inputs["gradebook"], inputs["grade_history"]
    except Exception:  # noqa: BLE001  mount down: check the upload on its own
        current_gb = current_hist = None
    result = await run_in_threadpool(U.check, inspected, code, current, current_gb is not None, current_gb,
                                     current_hist is not None)
    out = result.dict()
    if dry_run or not result.ok:
        return out
    return {**out, **await _commit(request, db, ad, ctx.course, code, inspected, ctx.user.upn)}


# ---------------------------------------------------------------- new course
@router.post("/uploads/courses")
async def upload_new_course(request: Request, files: list[UploadFile] = File(...), course_code: str = Form(""),
                            name: str = Form(""), dry_run: bool = Form(True), user: User = Depends(current_user),
                            db: Database = Depends(get_db)):
    """Add a Blackboard course to SPARK from its exports. The uploader gets Instructor access."""
    ad = _adapter(request)
    if not set(user.roles) & {"Instructor", "CourseDirector", "Admin"}:
        raise HTTPException(status_code=403, detail="Only instructors can add courses.")
    with _workdir(request) as tmpdir:
        return await _upload_new_course(request, files, course_code, name, dry_run, user, db, ad, tmpdir)


async def _upload_new_course(request, files, course_code, name, dry_run, user, db, ad, tmpdir):
    inspected = await _inspect(request, files, tmpdir)
    code = course_code.strip()
    result = await run_in_threadpool(U.check, inspected, code or None, None, False, None)
    out = result.dict()
    code = code or (result.suggested_code or "")
    out["course_code"] = code
    if code and not U.COURSE_CODE_RE.match(code):
        out["ok"] = False
        out["errors"].append("The course ID may use letters, digits, '.', '_' and '-' only (2 to 100 characters).")
    elif not code and result.ok:
        out["ok"] = False
        out["errors"].append("Enter the Blackboard course ID (it couldn't be read from the gradebook).")
    cid = make_course_id("bb", code) if code else None
    if cid:
        with db.session() as s:
            if s.get(Course, cid) is not None:
                out["ok"] = False
                out["errors"].append("This course ID can't be added. If the course is already in SPARK, ask its "
                                     "instructor or an administrator for access, then upload from its Data page.")
    if dry_run or not out["ok"]:
        return out
    display = name.strip()[:300] or code
    with db.session() as s:
        course = Course(id=cid, name=display, lms="bb", source=code)
        s.add(course)
        if not user.is_admin:
            s.add(CourseAccess(upn=user.upn, course_id=cid, role="Instructor"))
        s.commit()
    request.app.state.seed_grants()                 # local mode: the instructor sees courses an admin adds
    return {**out, "course_id": cid, **await _commit(request, db, ad, course, code, inspected, user.upn)}
