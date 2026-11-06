import duckdb
import pandas as pd
import pytest

from spark_core.analytics.director import rank_sections
from spark_core.etl.admissions import make_fl_name_key
from tests.conftest import BB_COURSE, as_user

H = as_user("instructor")


@pytest.fixture()
def ingested(client):
    assert client.post(f"/api/courses/{BB_COURSE}/ingest", headers=H).json()["run"]["status"] == "succeeded"
    return client


def test_ingest_stores_clusters(ingested):
    run = ingested.get(f"/api/courses/{BB_COURSE}/ingest-runs", headers=H).json()["runs"][0]
    from spark_api.services.ingest import SCHEMA_VERSION
    assert run["report"]["schema"] == SCHEMA_VERSION
    assert sum(run["report"]["risk_counts"].values()) == 54


def test_analytics_payload(ingested):
    a = ingested.get(f"/api/courses/{BB_COURSE}/analytics", headers=H).json()
    assert a["available"] and len(a["students"]) == 54
    labels = [s["cluster_label"] for s in a["summary"]]
    assert labels == ["Critical", "High", "Medium", "Low"]
    # risk order is by mean grade ratio, lowest first
    grades = [s["mean_grade_ratio"] for s in a["summary"]]
    assert grades == sorted(grades)
    assert a["admissions"]["available"] is False


@pytest.mark.parametrize("name", ["pca2d", "pca3d", "grade_violin", "avg_violin", "missed_late",
                                  "perf_consistency", "days_since", "pace"])
def test_analytics_figures(ingested, name):
    r = ingested.get(f"/api/courses/{BB_COURSE}/analytics/figures/{name}", params={"student_id": "stu0005"}, headers=H)
    assert r.status_code == 200 and r.json()["data"]


def test_student_detail_includes_risk(ingested):
    st = ingested.get(f"/api/courses/{BB_COURSE}/students/stu0005", headers=H).json()
    assert st["kpis"]["risk_label"] in {"Critical", "High", "Medium", "Low"}


def test_risk_report_csv_is_audited(ingested, settings):
    r = ingested.get(f"/api/courses/{BB_COURSE}/analytics/report.csv", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("ID,Name,Section") and len(lines) == 55
    from sqlalchemy import select
    from spark_api.db import AuditEvent, Database
    with Database(settings.resolved_database_url()).session() as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "export_risk_report")).first()


def test_director_summary_and_ranking(ingested):
    d = ingested.get(f"/api/courses/{BB_COURSE}/director", headers=H).json()
    assert d["kpis"]["sections"] == 3 and d["kpis"]["students"] == 54
    assert [s["RANK"] for s in d["sections"]] == [1, 2, 3]
    late = ingested.get(f"/api/courses/{BB_COURSE}/director", params={"metric": "LATE_RATE"}, headers=H).json()
    rates = [s["LATE_RATE"] for s in late["sections"]]
    assert rates == sorted(rates)          # lower late rate ranks first
    for fig in ("rank_bar", "grade_box"):
        assert ingested.get(f"/api/courses/{BB_COURSE}/director/figures/{fig}", headers=H).json()["data"]


def test_director_section_roster(ingested):
    d = ingested.get(f"/api/courses/{BB_COURSE}/director", headers=H).json()
    sec = next(s for s in d["sections"] if s["SECTION_NAME"] == "T3A")
    r = ingested.get(f"/api/courses/{BB_COURSE}/director/sections/{sec['SECTION_ID']}", headers=H).json()
    assert r["section_name"] == "T3A" and len(r["students"]) == 12
    hi = [s["HEALTH_INDEX"] for s in r["students"]]
    assert hi == sorted(hi)                # least healthy first


def test_analytics_requires_course_access(ingested):
    assert ingested.get(f"/api/courses/{BB_COURSE}/analytics", headers=as_user("noaccess")).status_code == 404
    assert ingested.get(f"/api/courses/{BB_COURSE}/director", headers=as_user("noaccess")).status_code == 404


def test_admissions_enrichment_when_db_present(ingested, settings):
    # Build a tiny admissions DB that matches two anonymized students by name key.
    path = settings.data_dir / "admissions.duckdb"
    con = duckdb.connect(str(path))
    adm = pd.DataFrame({"FL_NAME_KEY": [make_fl_name_key("Demo Student0001"), make_fl_name_key("Demo Student0002")],
                        "CMPAC": [3100, 2800], "SAT_MATH": [700, 620], "INTERNATIONAL": ["No", "Yes"],
                        "PROG_EXPERIENCE": ["None", "One Semester"]})
    con.register("adm", adm)
    con.execute("CREATE TABLE DIM_ADMISSIONS AS SELECT * FROM adm")
    con.close()
    a = ingested.get(f"/api/courses/{BB_COURSE}/analytics", headers=H).json()["admissions"]
    assert a["available"] and a["matched"] == 2 and "CMPAC" in a["numeric_fields"]
    for name, params in [("adm_numeric_box", {"metric": "CMPAC"}), ("adm_numeric_scatter", {"metric": "SAT_MATH"}),
                         ("adm_yesno", {}), ("adm_category", {"field": "PROG_EXPERIENCE", "stacked": True})]:
        r = ingested.get(f"/api/courses/{BB_COURSE}/analytics/figures/{name}", params=params, headers=H)
        assert r.status_code == 200, (name, r.text)


def test_name_key_matches_original_rules():
    assert make_fl_name_key("Smith, Jane A.") == "janeasmith"
    assert make_fl_name_key("C25 Jane Smith") == "janesmith"


def test_rank_sections_rejects_unknown_metric():
    with pytest.raises(ValueError):
        rank_sections(pd.DataFrame(), "NOPE")
