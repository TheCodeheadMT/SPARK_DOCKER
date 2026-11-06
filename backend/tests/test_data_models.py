import io
import zipfile

import pandas as pd
import pytest

from tests.conftest import BB_COURSE, as_user

H = as_user("instructor")


@pytest.fixture()
def ingested(client):
    assert client.post(f"/api/courses/{BB_COURSE}/ingest", headers=H).json()["run"]["status"] == "succeeded"
    return client


# ------------------------------------------------------------------ synthetic files
def admissions_xlsx(rows: list[tuple[str, str, int]]) -> bytes:
    """Mimics AdmissionsData.xlsx: a title row, then headers on row 2."""
    df = pd.DataFrame([{"ID": i, "First Name": f, "Last": l, "Sex": "F", "CMPAC Score": c, "SAT MATH": 650,
                        "Internatio Flag": "N", "Best Describe Progrm Exp:": "One semester",
                        "USAFA Race": "X", "Primary Sport": "Soccer"} for i, (f, l, c) in enumerate(rows)])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["Admissions Data Export"]]).to_excel(xw, index=False, header=False, startrow=0)
        df.to_excel(xw, index=False, startrow=1)
    return buf.getvalue()


def zybooks_csv() -> bytes:
    """Mimics the zyBooks Class Roster Report: 3 header rows (category / chapter / metric)."""
    cols = [("Student", "", "First name"), ("Student", "", "Last name"), ("Student", "", "Email"),
            ("Completion", "1. Intro", "Participation"), ("Completion", "1. Intro", "zyLab"),
            ("Time spent", "1. Intro", "Minutes"),
            ("Completion", "2. Loops", "Participation"), ("Completion", "2. Loops", "zyLab"),
            ("Time spent", "2. Loops", "Minutes")]
    rows = [["Demo", "Student0001", "stu0001@example.edu", "100%", "90%", "45", "80%", "70%", "60"],
            ["Demo", "Student0002", "STU0002@Example.edu", "50%", "-", "10", "40%", "20%", "15"],
            ["Nobody", "Here", "ghost@example.edu", "10%", "10%", "1", "10%", "10%", "1"]]
    df = pd.DataFrame(rows, columns=pd.MultiIndex.from_tuples(cols))
    return df.to_csv(index=False).encode()


# ------------------------------------------------------------------ success model
def test_train_reports_cv_metrics_and_predicts(ingested):
    r = ingested.post(f"/api/courses/{BB_COURSE}/models/train", headers=H,
                      json={"success_threshold": 85, "classifier": "rf"})
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert m["n_students"] == 54 and m["metrics"]["folds"] == 5 and 0.5 < m["metrics"]["roc_auc"] <= 1
    assert m["importance"][0]["importance"] >= m["importance"][-1]["importance"]
    st = ingested.get(f"/api/courses/{BB_COURSE}/students/stu0005", headers=H).json()
    assert 0 <= st["kpis"]["success_prob"] <= 1 and st["model"]["aliased"] is False
    roster = ingested.get(f"/api/courses/{BB_COURSE}/students", headers=H).json()
    assert all(s["success_prob"] is not None for s in roster["students"])


def test_progress_features_used_at_prediction():
    """Regression: the Streamlit predict step skipped progress features (always imputed)."""
    from spark_core.ml import success as S
    feats = pd.DataFrame({"STUDENT_ID": ["a"], "num_assignments": [9], "total_possible": [300.0]})
    out = S.add_progress_features(feats, {"total_assignments": 18.0, "course_points_possible": 600.0})
    assert out["progress_assignments_pct"].iat[0] == 50.0 and out["progress_points_pct"].iat[0] == 50.0


def test_single_class_threshold_is_explained(ingested):
    r = ingested.post(f"/api/courses/{BB_COURSE}/models/train", headers=H, json={"success_threshold": 10})
    assert r.status_code == 422 and "SUCCESS" in r.json()["detail"]


def test_tiny_minority_trains_but_flags_unvalidated(ingested):
    r = ingested.post(f"/api/courses/{BB_COURSE}/models/train", headers=H, json={"success_threshold": 72})
    assert r.status_code == 200 and r.json()["model"]["metrics"]["folds"] == 0


def test_alias_cross_course_reuse(ingested):
    d = as_user("director")
    assert ingested.post("/api/courses/fixture:DEMO001/ingest", headers=d).json()["run"]["status"] == "succeeded"
    assert ingested.post(f"/api/courses/{BB_COURSE}/models/train", headers=d, json={"success_threshold": 85}).status_code == 200
    # instructor can alias DEMO001 -> BB course (has access to both)
    r = ingested.put("/api/courses/fixture:DEMO001/models/alias", headers=H, json={"source_course_id": BB_COURSE})
    assert r.json()["alias_source"] == BB_COURSE
    v = ingested.get("/api/courses/fixture:DEMO001/models", headers=H).json()
    assert v["used"]["course_id"] == BB_COURSE and v["own"] is None
    st = ingested.get("/api/courses/fixture:DEMO001/students/demo0001", headers=H).json()
    assert st["model"]["aliased"] and st["kpis"]["success_prob"] is not None
    # cannot alias to a course you cannot see
    assert ingested.put("/api/courses/fixture:DEMO001/models/alias", headers=H,
                        json={"source_course_id": "fixture:DEMO002"}).status_code == 404
    ingested.put("/api/courses/fixture:DEMO001/models/alias", headers=H, json={"source_course_id": None})
    assert ingested.get("/api/courses/fixture:DEMO001/models", headers=H).json()["used"] is None


# ------------------------------------------------------------------ data page / export
def test_course_data_and_audited_export(ingested, settings):
    d = ingested.get(f"/api/courses/{BB_COURSE}/data", headers=H).json()
    assert d["has_data"] and d["runs"][0]["status"] == "succeeded" and d["zybooks"] == {"exists": False}
    r = ingested.get(f"/api/courses/{BB_COURSE}/data/export.duckdb", headers=H)
    assert r.status_code == 200 and len(r.content) > 1000
    from sqlalchemy import select
    from spark_api.db import AuditEvent, Database
    with Database(settings.resolved_database_url()).session() as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "export_course_db")).first()


# ------------------------------------------------------------------ zyBooks
def test_zybooks_upload_matches_and_charts(ingested):
    r = ingested.post(f"/api/courses/{BB_COURSE}/zybooks", headers=H,
                      files={"file": ("roster.csv", zybooks_csv(), "text/csv")})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["people"] == 3 and res["matched"] == 2 and res["matched_by"] == {"username": 2}
    s = ingested.get(f"/api/courses/{BB_COURSE}/zybooks/students/stu0001", headers=H).json()
    assert s["available"] and s["kpis"]["total_minutes"] == 105 and s["kpis"]["avg_zylab"] == 80
    assert ingested.get(f"/api/courses/{BB_COURSE}/zybooks/students/stu0001/figure", headers=H).json()["data"]
    assert ingested.get(f"/api/courses/{BB_COURSE}/zybooks/matrix", headers=H).json()["data"]
    assert ingested.get(f"/api/courses/{BB_COURSE}/zybooks/students/stu0009", headers=H).json() == {"available": False}


def test_zybooks_is_per_course(ingested):
    d = as_user("director")
    ingested.post("/api/courses/fixture:DEMO001/ingest", headers=d)
    ingested.post(f"/api/courses/{BB_COURSE}/zybooks", headers=H, files={"file": ("a.csv", zybooks_csv(), "text/csv")})
    ingested.post("/api/courses/fixture:DEMO001/zybooks", headers=d, files={"file": ("b.csv", zybooks_csv(), "text/csv")})
    # uploading to DEMO001 must not erase the BB course's data (the Streamlit version did)
    assert ingested.get(f"/api/courses/{BB_COURSE}/zybooks/students/stu0001", headers=H).json()["available"]


def test_zybooks_rejects_bad_files(ingested):
    r = ingested.post(f"/api/courses/{BB_COURSE}/zybooks", headers=H, files={"file": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    r = ingested.post(f"/api/courses/{BB_COURSE}/zybooks", headers=H, files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 422


# ------------------------------------------------------------------ admissions (admin)
def test_admissions_upload_is_admin_only(client):
    x = admissions_xlsx([("Demo", "Student0001", 3100)])
    assert client.post("/api/admin/admissions", headers=as_user("director"),
                       files={"file": ("a.xlsx", x)}).status_code == 403


def test_admissions_append_only_dedup_and_analytics(ingested):
    A = as_user("admin")
    x1 = admissions_xlsx([("Demo", "Student0001", 3100), ("Demo", "Student0002", 2800)])
    dry = ingested.post("/api/admin/admissions?dry_run=true", headers=A, files={"file": ("a.xlsx", x1)}).json()
    assert dry["results"][0]["new_rows"] == 2 and dry["status"]["exists"] is False      # dry run wrote nothing
    r = ingested.post("/api/admin/admissions", headers=A, files={"file": ("a.xlsx", x1)}).json()
    assert r["results"][0]["status"] == "ingested" and r["status"]["students"] == 2
    again = ingested.post("/api/admin/admissions", headers=A, files={"file": ("a.xlsx", x1)}).json()
    assert again["results"][0]["status"].startswith("skipped")
    # zip with an overlapping + a new student: only the new one is added
    x2 = admissions_xlsx([("Demo", "Student0002", 9999), ("Demo", "Student0003", 2500)])
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("2027/AdmissionsData.xlsx", x2)
    r = ingested.post("/api/admin/admissions", headers=A, files={"file": ("batch.zip", zbuf.getvalue())}).json()
    assert r["results"][0]["new_rows"] == 1 and r["status"]["students"] == 3
    a = ingested.get(f"/api/courses/{BB_COURSE}/analytics", headers=H).json()["admissions"]
    assert a["available"] and a["matched"] == 3
    # sensitive columns are stored for compatibility but never offered to the UI
    assert "RACE" not in a["category_fields"] and "SEX" not in a["category_fields"]


def test_admin_inventory(ingested):
    inv = ingested.get("/api/admin/inventory", headers=as_user("admin")).json()
    bb = next(c for c in inv["courses"] if c["id"] == BB_COURSE)
    assert bb["has_data"] and bb["students"] == 54 and bb["model"] is False
    assert ingested.get("/api/admin/inventory", headers=H).status_code == 403


def _legacy_admissions_db(path, marker_sex, marker_race):
    """An admissions.duckdb as the Streamlit app wrote it, including SEX and RACE."""
    import duckdb
    con = duckdb.connect(str(path))
    df = pd.DataFrame({"ID": ["1", "2"], "NAME_KEY_FL": ["demo student0001", "demo student0002"],
                       "FL_NAME_KEY": ["demostudent0001", "demostudent0002"],
                       "SEX": [marker_sex, marker_sex], "RACE": [marker_race, marker_race],
                       "CMPAC": [3000, 2900], "SAT_MATH": [700, 600]})
    con.register("df", df)
    con.execute("CREATE TABLE DIM_ADMISSIONS AS SELECT * FROM df")
    con.execute("""CREATE TABLE DIM_ADMISSIONS_FILE_LOG (FILE_NAME VARCHAR, FILE_HASH VARCHAR, ADDED_AT TIMESTAMP,
                   ROWS_IN_FILE INTEGER, NEW_ROWS_INSERTED INTEGER, TOTAL_ROWS_AFTER INTEGER)""")
    con.execute("INSERT INTO DIM_ADMISSIONS_FILE_LOG VALUES ('old.xlsx', 'abc', NOW(), 2, 2, 2)")
    con.close()


def test_new_uploads_never_store_sex_or_race(ingested, settings):
    import duckdb
    A = as_user("admin")
    ingested.post("/api/admin/admissions", headers=A, files={"file": ("a.xlsx", admissions_xlsx([("Demo", "Student0001", 3100)]))})
    con = duckdb.connect(str(settings.data_dir / "admissions.duckdb"), read_only=True)
    cols = set(con.execute("PRAGMA table_info('DIM_ADMISSIONS')").fetchdf()["name"])
    con.close()
    assert not {"SEX", "RACE"} & cols


def test_scrub_removes_legacy_columns_and_values(ingested, settings):
    path = settings.data_dir / "admissions.duckdb"
    marker_sex, marker_race = "SEXMARKER" + "Q7Z3W9" * 30, "RACEMARKER" + "K2J8V4" * 30
    _legacy_admissions_db(path, marker_sex, marker_race)
    A = as_user("admin")
    st = ingested.get("/api/admin/admissions", headers=A).json()
    assert st["legacy_sensitive_columns"] == ["RACE", "SEX"] and st["students"] == 2
    r = ingested.post("/api/admin/admissions/scrub", headers=A).json()
    assert sorted(r["dropped"]) == ["RACE", "SEX"] and r["status"]["legacy_sensitive_columns"] == []
    assert r["status"]["students"] == 2 and r["status"]["files"] == 1          # nothing else lost
    raw = path.read_bytes()
    assert b"SEXMARKER" not in raw and b"RACEMARKER" not in raw
    assert not list(settings.data_dir.glob("admissions.duckdb.*"))             # no temp files left behind
    # analytics still enriches from the scrubbed file
    a = ingested.get(f"/api/courses/{BB_COURSE}/analytics", headers=H).json()["admissions"]
    assert a["available"] and a["matched"] == 2
    assert ingested.post("/api/admin/admissions/scrub", headers=A).json()["dropped"] == []
    assert ingested.post("/api/admin/admissions/scrub", headers=H).status_code == 403


def test_upload_into_legacy_db_also_scrubs(ingested, settings):
    path = settings.data_dir / "admissions.duckdb"
    _legacy_admissions_db(path, "SEXMARKER" + "A1" * 60, "RACEMARKER" + "B2" * 60)
    r = ingested.post("/api/admin/admissions", headers=as_user("admin"),
                      files={"file": ("n.xlsx", admissions_xlsx([("Demo", "Student0003", 2500)]))}).json()
    assert r["status"]["students"] == 3 and r["status"]["legacy_sensitive_columns"] == []
    raw = path.read_bytes()
    assert b"SEXMARKER" not in raw and b"RACEMARKER" not in raw
