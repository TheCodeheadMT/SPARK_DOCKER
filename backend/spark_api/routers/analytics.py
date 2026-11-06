"""Analytics (risk clusters) and Course Director endpoints."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from spark_api.db import Database
from spark_api.deps import CourseContext, course_access, get_db
from spark_api.routers.courses import _audit, _db_file
from spark_api.services import analytics as svc
from spark_api.services.queries import records
from spark_core.analytics.director import HEALTH_WEIGHTS, METRICS, rank_sections
from spark_core.ml.clustering import FEATURE_COLS, cluster_summary
from spark_core.plotting import PlotSettings
from spark_core import plotting_analytics as pa

router = APIRouter(prefix="/api/courses/{course_id}")


def _settings(cutoff: float, show_cutoff: bool) -> PlotSettings:
    return PlotSettings(cutoff_percentage=cutoff, show_cutoff=show_cutoff)


def _fig(fig) -> Response:
    return Response(content=fig.to_json(), media_type="application/json")


def _clusters_or_409(request: Request, ctx: CourseContext):
    f = _db_file(request, ctx)
    df = svc.load_clusters(f)
    if df is None:
        raise HTTPException(status_code=409, detail="Risk clusters are not available for this course yet. "
                                                    "Re-run the ingest, or the course may be too small.")
    return df


def _admissions_db(request: Request):
    return request.app.state.settings.data_dir / "admissions.duckdb"


# ---------------------------------------------------------------- analytics
@router.get("/analytics")
def analytics(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
              db: Database = Depends(get_db)):
    f = _db_file(request, ctx)
    df = svc.load_clusters(f)
    if df is None:
        return {"available": False, "note": "Risk clusters are not available yet (re-run ingest, or the course "
                                            "has too few graded students).", "students": [], "summary": []}
    df, adm = svc.with_admissions(df, _admissions_db(request))
    _audit(db, ctx.user, "view_analytics", course_id)
    cols = ["STUDENT_ID", "STUDENT_NAME", "SECTION_NAME", "cluster_label", "risk_rank", *FEATURE_COLS,
            "num_assignments", "n_timed_items", "days_since_last_submission", "pace"]
    return {"available": True, "features": FEATURE_COLS, "summary": records(cluster_summary(df)),
            "students": records(df[cols]), "admissions": adm}


@router.get("/analytics/figures/{name}")
def analytics_figure(name: str, request: Request, student_id: str | None = None,
                     metric: str | None = None, field: str | None = None, stacked: bool = False,
                     ctx: CourseContext = Depends(course_access)):
    df = _clusters_or_409(request, ctx)
    s = PlotSettings()
    if name == "pca2d":
        return _fig(pa.clusters_2d(df, student_id, s))
    if name == "pca3d":
        return _fig(pa.clusters_3d(df, student_id, s))
    if name in pa.INSIGHT_FIGURES:
        return _fig(pa.insight_figure(name, df, student_id, s))
    if name.startswith("adm_"):
        df, adm = svc.with_admissions(df, _admissions_db(request))
        if not adm["available"]:
            raise HTTPException(status_code=409, detail="No admissions data matched this course.")
        if name in ("adm_numeric_box", "adm_numeric_scatter"):
            if metric not in adm["numeric_fields"]:
                raise HTTPException(status_code=400, detail="Unknown admissions metric")
            box, sc = pa.admissions_numeric(df, metric, adm["numeric_fields"][metric], student_id, s)
            return _fig(box if name == "adm_numeric_box" else sc)
        if name == "adm_yesno":
            return _fig(pa.admissions_yesno(df, adm["yesno_fields"], s))
        if name == "adm_category":
            if field not in adm["category_fields"]:
                raise HTTPException(status_code=400, detail="Unknown admissions field")
            return _fig(pa.admissions_category(df, field, adm["category_fields"][field], stacked, s))
    raise HTTPException(status_code=404, detail="Unknown figure")


@router.get("/analytics/report.csv")
def analytics_report(course_id: str, request: Request, ctx: CourseContext = Depends(course_access),
                     db: Database = Depends(get_db)):
    df = _clusters_or_409(request, ctx)
    out = df.rename(columns={
        "STUDENT_ID": "ID", "STUDENT_NAME": "Name", "SECTION_NAME": "Section", "total_points": "Total_Points",
        "total_possible": "Possible_Points", "num_assignments": "Num_Assignments", "grade_ratio": "Grade_Ratio",
        "avg_percentage": "Avg_Percentage", "std_percentage": "Std_Percentage", "pace": "Pace_Assignments_Per_Week",
        "weeks_active": "Weeks_Active", "late_rate": "Late_Rate", "on_time_rate": "On_Time_Rate",
        "missed_rate": "Missed_Rate", "n_timed_items": "Timed_Items",
        "mean_submission_delta_days": "Avg_Days_Late_or_Early",
        "days_since_last_submission": "Days_Since_Last_Submission", "cluster": "Cluster_Index",
        "cluster_label": "Risk_Level"})
    keep = ["ID", "Name", "Section", "Total_Points", "Possible_Points", "Num_Assignments", "Grade_Ratio",
            "Avg_Percentage", "Std_Percentage", "Pace_Assignments_Per_Week", "Weeks_Active", "Late_Rate",
            "On_Time_Rate", "Missed_Rate", "Timed_Items", "Avg_Days_Late_or_Early", "Days_Since_Last_Submission",
            "Cluster_Index", "Risk_Level"]
    buf = io.StringIO()
    out[keep].to_csv(buf, index=False)
    _audit(db, ctx.user, "export_risk_report", course_id)
    fname = f"student_risk_report_{ctx.course.name}.csv".replace(" ", "_")
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------------------------------------------------------------- course director
@router.get("/director")
def director(course_id: str, request: Request, metric: str = "HEALTH_INDEX",
             cutoff: float = Query(60.0, ge=0, le=100), ctx: CourseContext = Depends(course_access),
             db: Database = Depends(get_db)):
    if metric not in METRICS:
        raise HTTPException(status_code=400, detail="Unknown metric")
    roster, summary = svc.director_summary(_db_file(request, ctx), cutoff)
    ranked = rank_sections(summary, metric)
    health = rank_sections(summary, "HEALTH_INDEX")
    _audit(db, ctx.user, "view_director", course_id)
    return {
        "metric": metric, "metrics": METRICS, "weights": HEALTH_WEIGHTS, "cutoff": cutoff,
        "kpis": {"sections": int(summary["SECTION_ID"].nunique()), "students": int(roster["STUDENT_ID"].nunique()),
                 "top_section": health["SECTION_NAME"].iat[0] if len(health) else None,
                 "lowest_section": health["SECTION_NAME"].iat[-1] if len(health) else None,
                 "below_cutoff": int((roster.drop_duplicates("STUDENT_ID")["GRADE_PCT"] < cutoff).sum())},
        "sections": records(ranked),
    }


@router.get("/director/figures/{name}")
def director_figure(name: str, request: Request, metric: str = "HEALTH_INDEX",
                    cutoff: float = Query(60.0, ge=0, le=100), show_cutoff: bool = True,
                    ctx: CourseContext = Depends(course_access)):
    roster, summary = svc.director_summary(_db_file(request, ctx), cutoff)
    s = _settings(cutoff, show_cutoff)
    if name == "rank_bar":
        if metric not in METRICS:
            raise HTTPException(status_code=400, detail="Unknown metric")
        return _fig(pa.section_metric_bar(rank_sections(summary, metric), metric, METRICS[metric]["label"], s))
    if name == "grade_box":
        return _fig(pa.section_grade_box(roster, s))
    raise HTTPException(status_code=404, detail="Unknown figure")


@router.get("/director/sections/{section_id}")
def director_section(course_id: str, section_id: str, request: Request,
                     ctx: CourseContext = Depends(course_access), db: Database = Depends(get_db)):
    roster = svc.director_roster(_db_file(request, ctx))
    sec = roster[roster["SECTION_ID"] == section_id]
    if sec.empty:
        raise HTTPException(status_code=404, detail="Section not found")
    _audit(db, ctx.user, "view_section_roster", course_id, section_id)
    cols = ["STUDENT_ID", "STUDENT_NAME", "HEALTH_INDEX", "GRADE_PCT", "CURRENT_GRADE", "avg_percentage",
            "late_rate", "missed_rate", "n_timed_items", "cluster_label", "ENROLLMENT_STATE"]
    return {"section_id": section_id, "section_name": sec["SECTION_NAME"].iat[0],
            "students": records(sec.sort_values("HEALTH_INDEX")[cols])}
