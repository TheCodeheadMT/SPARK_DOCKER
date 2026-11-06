"""Extra Instruction (EI): triage, prompt preview, AI generation (streamed), saved sessions, exports."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from spark_api.db import AuditEvent, Database, EISession
from spark_api.deps import CourseContext, course_access, get_db
from spark_api.services import ei as E
from spark_api.services.llm import LLMService, LLMUnavailable, RateLimited, describe_error
from spark_core.ei import prompts as P
from spark_core.ei import signals as S
from spark_core.etl.store import course_db_path
from spark_core.ids import slug

log = logging.getLogger("spark.ei")
router = APIRouter(prefix="/api/courses/{course_id}/students/{student_id}/ei")


# ---------------------------------------------------------------- helpers
def _llm(request: Request) -> LLMService:
    return request.app.state.llm


def _state(request: Request, ctx: CourseContext, student_id: str) -> E.StudentState:
    f = course_db_path(request.app.state.settings.data_dir, ctx.course.id)
    if not f.exists():
        raise HTTPException(status_code=409, detail="This course has no data yet. Run an ingest first.")
    st = E.load_state(f, student_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Student not in this course")
    return st


def _audit(db: Database, upn: str, action: str, course_id: str, target: str) -> None:
    with db.session() as s:
        s.add(AuditEvent(upn=upn, action=action, course_id=course_id, target=target))
        s.commit()


def _session_out(x: EISession, full: bool = False) -> dict:
    d = {"id": x.id, "created_by": x.created_by, "created_at": x.created_at.isoformat() if x.created_at else None,
         "updated_at": x.updated_at.isoformat() if x.updated_at else None, "model": x.model,
         "has_plan": bool(x.plan_md), "has_workbook": bool(x.workbook_md),
         "objectives_preview": (x.objectives or "")[:120]}
    if full:
        d.update(objectives=x.objectives, instructor_notes=x.instructor_notes, student_notes=x.student_notes,
                 plan_md=x.plan_md, workbook_md=x.workbook_md, temperature=x.temperature,
                 workbook_config=x.workbook_config or {}, modules=x.modules or [], signals=x.signals or {})
    return d


def _get_session(db: Database, course_id: str, student_id: str, session_id: int) -> EISession:
    with db.session() as s:
        x = s.get(EISession, session_id)
        if x is None or x.course_id != course_id or x.student_id != student_id:
            raise HTTPException(status_code=404, detail="Session not found")
        return x


class Inputs(BaseModel):
    objectives: str = Field("", max_length=8000)
    instructor_notes: str = Field("", max_length=8000)
    student_notes: str = Field("", max_length=8000)


class WorkbookCfg(BaseModel):
    include_overview: bool = True
    include_student_snapshot: bool = True
    include_time_plan: bool = True
    include_study_plan: bool = True
    include_practice: bool = True
    include_reflection: bool = True
    include_resources: bool = True
    include_checklist: bool = True
    tone: str = "Professional"
    length: str = "Medium"
    custom_instructor_notes: str = Field("", max_length=4000)


class PromptReq(Inputs):
    kind: str = Field("plan", pattern="^(plan|workbook|objectives)$")
    workbook_config: WorkbookCfg = WorkbookCfg()
    module_keys: list[str] | None = None        # override the recommended modules


class GenerateReq(PromptReq):
    kind: str = Field("plan", pattern="^(plan|workbook)$")
    model: str | None = None
    temperature: float = Field(0.3, ge=0.0, le=1.0)
    session_id: int | None = None               # add to an existing session (e.g. workbook after plan)


class ModelReq(BaseModel):
    model: str | None = None
    temperature: float = Field(0.3, ge=0.0, le=1.0)


def _modules_for(st: E.StudentState, req: PromptReq) -> list[dict] | None:
    if req.kind != "workbook" or not req.module_keys:
        return None
    keys = list(dict.fromkeys(k for k in req.module_keys if k in S.MODULES))[:4]
    recs = {m["key"]: m["why"] for m in S.recommend_modules(st.signals, "critical", req.instructor_notes,
                                                             req.student_notes)}   # "critical" = rationale for 3
    return [{"key": k, "title": S.MODULES[k].title, "why": recs.get(k, "Selected by the instructor."),
             "student_outputs": list(S.MODULES[k].student_outputs)} for k in keys] or None


def _prompt(st: E.StudentState, ctx: CourseContext, req: PromptReq) -> dict:
    cfg = P.config_from_dict(req.workbook_config.model_dump())
    out = E.build_prompt(st, req.kind, ctx.course.name, req.objectives, req.instructor_notes, req.student_notes,
                         cfg, _modules_for(st, req))
    leaks = E.prompt_leaks(st, out["system"] + "\n" + out["user"])
    if leaks:  # should never happen after redaction; refuse rather than send
        raise HTTPException(status_code=422, detail="The prompt still contains a student identifier. "
                                                    "Edit the notes and try again.")
    return out


# ---------------------------------------------------------------- context
@router.get("")
def context(course_id: str, student_id: str, request: Request, ctx: CourseContext = Depends(course_access),
            db: Database = Depends(get_db)):
    st = _state(request, ctx, student_id)
    with db.session() as s:
        sessions = s.scalars(select(EISession).where(EISession.course_id == course_id,
                                                     EISession.student_id == student_id)
                             .order_by(EISession.id.desc()).limit(50)).all()
    _audit(db, ctx.user.upn, "ei_view", course_id, student_id)
    return {
        "student": {"id": student_id, "name": st.name},
        "as_of": st.as_of.isoformat() if st.as_of is not None else None,
        "signals": st.signals,
        "activity_index": st.activity,
        "risk_label": st.risk,
        "triage": E.triage(st),
        "recommended_modules": S.recommend_modules(st.signals, st.risk),
        "all_modules": [{"key": m.key, "title": m.title, "when_to_use": m.when_to_use,
                         "student_outputs": list(m.student_outputs)} for m in S.MODULES.values()],
        "workbook_options": {"tones": P.TONES, "lengths": P.LENGTHS,
                             "default": WorkbookCfg().model_dump()},
        "llm": _llm(request).info(),
        "sessions": [_session_out(x) for x in sessions],
    }


@router.get("/methodology")
def methodology(course_id: str, student_id: str, ctx: CourseContext = Depends(course_access)):
    return {"markdown": E.methodology()}


@router.post("/recommend")
def recommend(course_id: str, student_id: str, body: Inputs, request: Request,
              ctx: CourseContext = Depends(course_access)):
    """Modules re-ranked with the notes (keywords such as 'exam' or 'tutor' add modules)."""
    st = _state(request, ctx, student_id)
    return {"recommended_modules": S.recommend_modules(st.signals, st.risk, body.instructor_notes, body.student_notes)}


@router.post("/prompt")
def preview(course_id: str, student_id: str, body: PromptReq, request: Request,
            ctx: CourseContext = Depends(course_access)):
    """Exactly what would be sent to the AI model, after redaction. Works without an API key."""
    return _prompt(_state(request, ctx, student_id), ctx, body)


@router.post("/objectives")
async def objectives(course_id: str, student_id: str, body: ModelReq, request: Request,
                     ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    st = _state(request, ctx, student_id)
    pr = _prompt(st, ctx, PromptReq(kind="objectives"))
    llm = _llm(request)
    try:
        text = await run_in_threadpool(llm.complete, ctx.user.upn, pr["system"], pr["user"], body.model,
                                       body.temperature)
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after_s)})
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("objective generation failed")
        raise HTTPException(status_code=502, detail=describe_error(exc, body.model or llm.default_model))
    _audit(db, ctx.user.upn, "ei_objectives", course_id, student_id)
    return {"objectives": text.strip()}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/generate")
def generate(course_id: str, student_id: str, body: GenerateReq, request: Request,
             ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    """Stream a plan or workbook as server-sent events (meta, delta*, done | error), then save it."""
    st = _state(request, ctx, student_id)
    if body.kind == "plan" and not body.objectives.strip():
        raise HTTPException(status_code=422, detail="Learning objectives are required for an EI plan.")
    if body.session_id is not None:
        _get_session(db, course_id, student_id, body.session_id)
    pr = _prompt(st, ctx, body)
    llm = _llm(request)
    try:
        chunks = llm.stream(ctx.user.upn, pr["system"], pr["user"], body.model, body.temperature)
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after_s)})
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    model = body.model if body.model in llm.models else llm.default_model
    upn = ctx.user.upn

    def events():
        yield _sse("meta", {"kind": body.kind, "model": model, "redactions": pr["redactions"],
                            "modules": pr.get("modules")})
        parts: list[str] = []
        try:
            for c in chunks:
                parts.append(c)
                yield _sse("delta", c)
        except Exception as exc:
            log.exception("EI generation failed")
            yield _sse("error", {"detail": describe_error(exc, model)})
            return
        text = "".join(parts)
        with db.session() as s:
            x = s.get(EISession, body.session_id) if body.session_id else None
            if x is None:
                x = EISession(course_id=course_id, student_id=student_id, created_by=upn)
                s.add(x)
            x.objectives, x.instructor_notes, x.student_notes = body.objectives, body.instructor_notes, body.student_notes
            x.model, x.temperature, x.signals = model, body.temperature, st.signals
            if body.kind == "plan":
                x.plan_md = text
            else:
                x.workbook_md = text
                x.workbook_config = body.workbook_config.model_dump()
                x.modules = pr.get("modules") or []
            s.commit()
            sid = x.id
        _audit(db, upn, f"ei_generate_{body.kind}", course_id, student_id)
        yield _sse("done", {"session_id": sid, "chars": len(text)})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- sessions
@router.get("/sessions/{session_id}")
def get_session(course_id: str, student_id: str, session_id: int, ctx: CourseContext = Depends(course_access),
                db: Database = Depends(get_db)):
    return _session_out(_get_session(db, course_id, student_id, session_id), full=True)


@router.delete("/sessions/{session_id}")
def delete_session(course_id: str, student_id: str, session_id: int, ctx: CourseContext = Depends(course_access),
                   db: Database = Depends(get_db)):
    x = _get_session(db, course_id, student_id, session_id)
    if x.created_by != ctx.user.upn and ctx.role not in {"CourseDirector", "Admin"}:
        raise HTTPException(status_code=403, detail="Only the author or a course director can delete this session")
    with db.session() as s:
        s.delete(s.get(EISession, session_id))
        s.commit()
    _audit(db, ctx.user.upn, "ei_delete", course_id, f"{student_id}#{session_id}")
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/export")
def export(course_id: str, student_id: str, session_id: int, request: Request, part: str = "plan",
           fmt: str = "html", ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    if part not in {"plan", "workbook"} or fmt not in {"html", "pdf", "md"}:
        raise HTTPException(status_code=400, detail="part must be plan|workbook and fmt html|pdf|md")
    x = _get_session(db, course_id, student_id, session_id)
    md = x.plan_md if part == "plan" else x.workbook_md
    if not md:
        raise HTTPException(status_code=404, detail=f"This session has no {part} yet")
    st = _state(request, ctx, student_id)
    title = ("Extra Instruction Plan" if part == "plan" else "Student Workbook") + f" | {st.name}"
    when = (x.updated_at or x.created_at or datetime.now()).strftime("%Y-%m-%d")
    subtitle = f"{ctx.course.name} | {when} | {x.model}"
    fname = f"ei-{part}-{slug(course_id)}-{session_id}"
    try:
        from spark_core.ei.export import markdown_to_html, markdown_to_pdf_bytes
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Export needs a missing package ({exc.name}). "
                                                    "Run: pip install -r backend/requirements.txt")
    _audit(db, ctx.user.upn, "ei_export", course_id, f"{student_id}#{session_id}:{part}.{fmt}")
    if fmt == "pdf":
        data = markdown_to_pdf_bytes(md, title=title, subtitle=subtitle)
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})
    if fmt == "md":
        return Response(md, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})
    return Response(markdown_to_html(md, title, subtitle), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.html"'})
