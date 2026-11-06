"""Data & Models endpoints: course data management, success models, zyBooks, and the
admin-only admissions upload and inventory."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from spark_api.auth.base import User
from spark_api.db import Course, CourseAccess, Database, IngestRun, ModelAlias, SuccessModel
from spark_api.deps import CourseContext, course_access, get_db, require_admin, require_course_role
from spark_api.routers.courses import _audit, _db_file, _run_summary
from spark_api.services import models as msvc
from spark_api.services.queries import records
from spark_core import plotting_analytics as pa
from spark_core.etl import zybooks as zy
from spark_core.etl.admissions_loader import admissions_status, ingest_admissions, scrub_removed_columns
from spark_core.etl.store import course_db_path, query_df
from spark_core.ids import slug
from spark_core.ml.success import CLASSIFIER_LABELS, SuccessModelConfig, TrainingError

router = APIRouter(prefix="/api")
async def _read_upload(f: UploadFile, allowed: tuple[str, ...], limit_mb: int = 200) -> bytes:
    name = (f.filename or "").lower()
    if not name.endswith(allowed):
        raise HTTPException(status_code=400, detail=f"Expected a file ending in {', '.join(allowed)}")
    limit = limit_mb * 1024 * 1024
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File too large ({limit_mb} MB max)")
    return data


def _zy_path(request: Request, course_id: str):
    return request.app.state.settings.data_dir / "zybooks" / f"{slug(course_id)}.duckdb"


def _course_names(db: Database) -> dict[str, str]:
    with db.session() as s:
        return {c.id: c.name for c in s.scalars(select(Course))}


# ================================================================ course data
@router.get("/courses/{course_id}/data")
def course_data(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                db: Database = Depends(get_db)):
    f = course_db_path(request.app.state.settings.data_dir, course_id)
    with db.session() as s:
        runs = s.scalars(select(IngestRun).where(IngestRun.course_id == course_id)
                         .order_by(IngestRun.id.desc()).limit(15)).all()
        run_list = [_run_summary(r) for r in runs]
    return {"has_data": f.exists(), "db_size_mb": round(f.stat().st_size / 2**20, 2) if f.exists() else 0,
            "runs": run_list, "zybooks": zy.status(_zy_path(request, course_id)), "role": ctx.role}


@router.get("/courses/{course_id}/data/export.duckdb")
def export_db(course_id: str, request: Request, ctx: CourseContext = Depends(require_course_role("Instructor")),
              db: Database = Depends(get_db)):
    f = _db_file(request, ctx)
    _audit(db, ctx.user, "export_course_db", course_id)
    return FileResponse(f, media_type="application/octet-stream", filename=f"{slug(course_id)}.duckdb")


# ================================================================ success models
class TrainBody(BaseModel):
    success_threshold: float = Field(72.0, ge=0, le=100)
    include_grade_features: bool = False
    classifier: str = Field("hgb", pattern="^(hgb|rf|extra_trees)$")


class AliasBody(BaseModel):
    source_course_id: str | None = None


@router.get("/courses/{course_id}/models")
def models_view(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                db: Database = Depends(get_db)):
    names = _course_names(db)
    with db.session() as s:
        own = msvc.active_model(s, course_id)
        history = s.scalars(select(SuccessModel).where(SuccessModel.course_id == course_id)
                            .order_by(SuccessModel.id.desc()).limit(10)).all()
        alias = s.get(ModelAlias, course_id)
        # Reusable models: courses this user can access that have an active model.
        if ctx.user.is_admin:
            visible = set(names)
        else:
            visible = set(s.scalars(select(CourseAccess.course_id).where(CourseAccess.upn == ctx.user.upn)))
        candidates = [msvc.summarize(m, names) for m in s.scalars(select(SuccessModel).where(SuccessModel.active))
                      if m.course_id in visible and m.course_id != course_id]
    used, _ = msvc.resolve(db, course_id)
    return {"own": msvc.summarize(own, names), "used": msvc.summarize(used, names),
            "alias_source": alias.source_course_id if alias else None, "candidates": candidates,
            "history": [msvc.summarize(m, names) for m in history], "classifiers": CLASSIFIER_LABELS,
            "can_train": ctx.role in ("Instructor", "CourseDirector", "Admin")}


@router.post("/courses/{course_id}/models/train")
async def train_model(course_id: str, body: TrainBody, request: Request,
                      ctx: CourseContext = Depends(require_course_role("Instructor")), db: Database = Depends(get_db)):
    _db_file(request, ctx)
    cfg = SuccessModelConfig(success_threshold=body.success_threshold,
                             include_grade_features=body.include_grade_features, classifier=body.classifier)  # type: ignore[arg-type]
    try:
        m = await run_in_threadpool(msvc.train_for_course, db, request.app.state.settings.data_dir, ctx.course, cfg,
                                    ctx.user.upn)
    except TrainingError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _audit(db, ctx.user, "train_success_model", course_id, str(m.id))
    return {"model": msvc.summarize(m, _course_names(db))}


@router.put("/courses/{course_id}/models/alias")
def put_alias(course_id: str, body: AliasBody, ctx: CourseContext = Depends(require_course_role("Instructor")),
              db: Database = Depends(get_db)):
    src = body.source_course_id
    if src and src != course_id:
        with db.session() as s:
            if s.get(Course, src) is None:
                raise HTTPException(status_code=404, detail="Source course not found")
            allowed = ctx.user.is_admin or s.scalars(select(CourseAccess).where(
                CourseAccess.upn == ctx.user.upn, CourseAccess.course_id == src)).first()
            if not allowed:
                raise HTTPException(status_code=404, detail="Source course not found")
            if msvc.active_model(s, src) is None:
                raise HTTPException(status_code=409, detail="That course has no trained model")
    msvc.set_alias(db, course_id, src, ctx.user.upn)
    _audit(db, ctx.user, "set_model_alias", course_id, src or "")
    return {"alias_source": src if src and src != course_id else None}


# ================================================================ zyBooks
@router.post("/courses/{course_id}/zybooks")
async def upload_zybooks(course_id: str, request: Request, file: UploadFile = File(...),
                         ctx: CourseContext = Depends(require_course_role("Instructor")), db: Database = Depends(get_db)):
    data = await _read_upload(file, (".csv",), request.app.state.settings.upload_max_mb)
    roster = query_df(_db_file(request, ctx), "SELECT STUDENT_ID, LOGIN_ID, STUDENT_NAME FROM DIM_STUDENTS")
    try:
        activity = zy.match_students(zy.parse_report(data), roster)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"Could not read the zyBooks report: {e}")
    result = await run_in_threadpool(zy.write_course_zybooks, _zy_path(request, course_id), activity, file.filename or "")
    _audit(db, ctx.user, "upload_zybooks", course_id, file.filename or "")
    return {"result": result, "status": zy.status(_zy_path(request, course_id))}


@router.get("/courses/{course_id}/zybooks/students/{student_id}")
def zybooks_student(course_id: str, student_id: str, request: Request, ctx: CourseContext = Depends(course_access)):
    df = zy.student_detail(_zy_path(request, course_id), student_id)
    if df.empty:
        return {"available": False}
    return {"available": True,
            "kpis": {"avg_participation": float(df["Participation"].mean()), "avg_zylab": float(df["zyLab"].mean()),
                     "total_minutes": float(df["Minutes"].sum())},
            "chapters": records(df.drop(columns=["SortOrder"]))}


@router.get("/courses/{course_id}/zybooks/students/{student_id}/figure")
def zybooks_student_figure(course_id: str, student_id: str, request: Request,
                           ctx: CourseContext = Depends(course_access)):
    df = zy.student_detail(_zy_path(request, course_id), student_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="No zyBooks data for this student")
    return Response(pa.zybooks_combo(df).to_json(), media_type="application/json")


@router.get("/courses/{course_id}/zybooks/matrix")
def zybooks_matrix(course_id: str, request: Request, section_id: str | None = None,
                   ctx: CourseContext = Depends(course_access)):
    df = zy.course_matrix(_zy_path(request, course_id))
    if df.empty:
        raise HTTPException(status_code=404, detail="No zyBooks data for this course")
    roster = query_df(_db_file(request, ctx), """SELECT s.STUDENT_ID, s.STUDENT_NAME, m.SECTION_ID FROM DIM_STUDENTS s
                                                  JOIN V_SECTION_MEMBERSHIP m USING (STUDENT_ID)""")
    if section_id:
        df = df[df["STUDENT_ID"].isin(roster.loc[roster["SECTION_ID"] == section_id, "STUDENT_ID"])]
        if df.empty:
            raise HTTPException(status_code=404, detail="No zyBooks data for this section")
    names = dict(zip(roster["STUDENT_ID"], roster["STUDENT_NAME"]))
    return Response(pa.zybooks_matrix(df, names).to_json(), media_type="application/json")


# ================================================================ admin
def _adm_path(request: Request):
    return request.app.state.settings.data_dir / "admissions.duckdb"


@router.get("/admin/admissions")
def admissions(request: Request, user: User = Depends(require_admin)):
    return admissions_status(_adm_path(request))


@router.post("/admin/admissions")
async def upload_admissions(request: Request, dry_run: bool = False, file: UploadFile = File(...),
                            user: User = Depends(require_admin), db: Database = Depends(get_db)):
    data = await _read_upload(file, (".xlsx", ".zip"), request.app.state.settings.upload_max_mb)
    try:
        results = await run_in_threadpool(ingest_admissions, _adm_path(request), file.filename or "upload.xlsx",
                                          data, dry_run)
    except Exception as e:  # malformed workbook / zip
        raise HTTPException(status_code=422, detail=f"Could not read admissions file: {type(e).__name__}: {e}")
    if not dry_run:
        _audit(db, user, "upload_admissions", "", file.filename or "")
    return {"dry_run": dry_run, "results": results, "status": admissions_status(_adm_path(request))}


@router.post("/admin/admissions/scrub")
async def scrub_admissions(request: Request, user: User = Depends(require_admin), db: Database = Depends(get_db)):
    """Remove SEX/RACE columns left in an admissions DB copied from the Streamlit app."""
    dropped = await run_in_threadpool(scrub_removed_columns, _adm_path(request))
    if dropped:
        _audit(db, user, "scrub_admissions", "", ",".join(dropped))
    return {"dropped": dropped, "status": admissions_status(_adm_path(request))}


@router.get("/admin/inventory")
def inventory(request: Request, user: User = Depends(require_admin), db: Database = Depends(get_db)):
    data_dir = request.app.state.settings.data_dir
    out = []
    with db.session() as s:
        for c in s.scalars(select(Course).order_by(Course.name)):
            f = course_db_path(data_dir, c.id)
            last = s.scalars(select(IngestRun).where(IngestRun.course_id == c.id, IngestRun.status == "succeeded")
                             .order_by(IngestRun.id.desc()).limit(1)).first()
            model = msvc.active_model(s, c.id)
            alias = s.get(ModelAlias, c.id)
            zs = zy.status(_zy_path(request, c.id))
            out.append({"id": c.id, "name": c.name, "lms": c.lms, "has_data": f.exists(),
                        "db_size_mb": round(f.stat().st_size / 2**20, 2) if f.exists() else 0,
                        "last_ingest": last.finished_at.isoformat() if last and last.finished_at else None,
                        "students": (last.report or {}).get("students") if last else None,
                        "model": bool(model), "alias_source": alias.source_course_id if alias else None,
                        "zybooks": zs.get("exists", False)})
    return {"courses": out, "admissions": admissions_status(_adm_path(request))}
