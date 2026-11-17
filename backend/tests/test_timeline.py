"""Assignment timeline: assigned dates, first/last attempts, and the student API fields."""
import duckdb
import pandas as pd

from adapters.bb_ultra.parser import parse_course, read_bb_table, parse_grade_history
from spark_core.etl.store import course_db_path
from tests.conftest import BB_COURSE, FIXTURES, as_user

GB, GH = FIXTURES / "demo_gradebook.csv", FIXTURES / "demo_grade_history.csv"


def student_history() -> pd.DataFrame:
    h = parse_grade_history(read_bb_table(GH))
    return h[(h["ROLE"] == "S") & h["Attempt Activity"].notna()]


def test_attempts_keep_first_and_last():
    p = parse_course(GB, GH, "bb:T")
    att, subs = p["attempts"], p["submissions"]
    assert list(att.columns) == ["STUDENT_ID", "ASSIGNMENT_ID", "FIRST_ATTEMPT_AT", "LAST_ATTEMPT_AT", "ATTEMPTS"]
    multi = att[att["ATTEMPTS"] > 1]
    assert len(multi) > 0
    assert (multi["FIRST_ATTEMPT_AT"] < multi["LAST_ATTEMPT_AT"]).all()
    # the last attempt is the submission time the rest of SPARK already uses
    m = multi.merge(subs, on=["STUDENT_ID", "ASSIGNMENT_ID"])
    assert len(m) == len(multi) and (m["LAST_ATTEMPT_AT"] == m["SUBMITTED_AT"]).all()
    single = att[att["ATTEMPTS"] == 1]
    assert (single["FIRST_ATTEMPT_AT"] == single["LAST_ATTEMPT_AT"]).all()


def test_assigned_estimated_from_first_class_submission():
    a = parse_course(GB, GH, "bb:T")["assignments"].set_index("ASSIGNMENT_NAME")
    h = student_history()
    first_quiz1 = h.loc[h["Column"] == "Quiz 1", "Attempt Activity"].min()
    assert a.loc["Quiz 1", "ASSIGNED_SOURCE"] == "estimated_first_submission"
    assert a.loc["Quiz 1", "ASSIGNED_AT"] == first_quiz1
    assert a.loc["Quiz 1", "ASSIGNED_AT"] < a.loc["Quiz 1", "DUE_AT"]
    # in-class items have no student submissions, so nothing to estimate from
    assert a.loc["Graded Review 1", "ASSIGNED_SOURCE"] == "none" and pd.isna(a.loc["Graded Review 1", "ASSIGNED_AT"])


def test_manifest_assigned_overrides_and_blank_due_keeps_inferred():
    m = pd.DataFrame({"ASSIGNMENT_NAME": ["Quiz 1"], "DUE_AT": [""], "ASSIGNED_AT": ["2026-12-16 08:00"]})
    a = parse_course(GB, GH, "bb:T", manifest=m)["assignments"].set_index("ASSIGNMENT_NAME")
    assert a.loc["Quiz 1", "ASSIGNED_SOURCE"] == "manifest"
    assert a.loc["Quiz 1", "ASSIGNED_AT"] == pd.Timestamp("2026-12-16 08:00")
    assert a.loc["Quiz 1", "DUE_SOURCE"] == "inferred_auto_zero"
    assert a.loc["Quiz 1", "DUE_AT"] == pd.Timestamp("2026-12-23 23:59")


def test_manifest_with_only_assigned_column():
    m = pd.DataFrame({"ASSIGNMENT_NAME": ["Homework 2"], "ASSIGNED_AT": ["2026-12-18"]})
    a = parse_course(GB, GH, "bb:T", manifest=m)["assignments"].set_index("ASSIGNMENT_NAME")
    assert a.loc["Homework 2", "ASSIGNED_AT"] == pd.Timestamp("2026-12-18")
    assert a.loc["Homework 2", "DUE_SOURCE"] == "inferred_auto_zero"


def test_estimate_at_or_after_due_is_dropped():
    # A due date before anyone submitted: the class's first submission says nothing about release.
    m = pd.DataFrame({"ASSIGNMENT_NAME": ["Quiz 1"], "DUE_AT": ["2026-12-01 23:59"]})
    a = parse_course(GB, GH, "bb:T", manifest=m)["assignments"].set_index("ASSIGNMENT_NAME")
    assert a.loc["Quiz 1", "ASSIGNED_SOURCE"] == "none" and pd.isna(a.loc["Quiz 1", "ASSIGNED_AT"])


def test_no_grade_history_gives_empty_timeline():
    p = parse_course(GB, None, "bb:T")
    assert p["attempts"].empty and list(p["attempts"].columns)[0] == "STUDENT_ID"
    assert (p["assignments"]["ASSIGNED_SOURCE"] == "none").all()


def test_student_api_returns_timeline_fields(client, settings):
    h = as_user("instructor")
    run = client.post(f"/api/courses/{BB_COURSE}/ingest", headers=h).json()["run"]
    assert run["report"]["assigned_dates_estimated"] > 0
    rows = client.get(f"/api/courses/{BB_COURSE}/students/stu0001", headers=h).json()["assignments"]
    for r in rows:
        assert {"ASSIGNED_AT", "ASSIGNED_SOURCE", "FIRST_ATTEMPT_AT", "ATTEMPTS"} <= set(r)
    online = [r for r in rows if r["SUBMITTED_AT"]]
    assert online and all(r["FIRST_ATTEMPT_AT"] and r["ATTEMPTS"] >= 1 for r in online)
    assert all(r["FIRST_ATTEMPT_AT"] <= r["SUBMITTED_AT"] for r in online)
    assert any(r["ASSIGNED_SOURCE"] == "estimated_first_submission" for r in rows)

    # A course DB built before the timeline existed (schema 3) still serves the student page.
    db_file = course_db_path(settings.data_dir, BB_COURSE)
    con = duckdb.connect(str(db_file))
    con.execute("DROP TABLE SUBMISSION_ATTEMPTS")
    con.close()
    old = client.get(f"/api/courses/{BB_COURSE}/students/stu0001", headers=h)
    assert old.status_code == 200
    assert all(r["FIRST_ATTEMPT_AT"] is None and r["ATTEMPTS"] is None for r in old.json()["assignments"])


def test_fixture_course_has_no_timeline_data(client):
    h = as_user("director")
    assert client.post("/api/courses/fixture:DEMO002/ingest", headers=h).json()["run"]["status"] == "succeeded"
    sid = client.get("/api/courses/fixture:DEMO002/students", headers=h).json()["students"][0]["STUDENT_ID"]
    rows = client.get(f"/api/courses/fixture:DEMO002/students/{sid}", headers=h).json()["assignments"]
    assert rows and all(r["ASSIGNED_SOURCE"] == "none" and r["FIRST_ATTEMPT_AT"] is None for r in rows)
