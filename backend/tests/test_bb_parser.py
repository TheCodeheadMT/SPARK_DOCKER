import pandas as pd

from adapters.bb_ultra.parser import infer_due_dates, parse_course, parse_gradebook_columns, validation_report_dict
from spark_core.etl.transform import transform_data
from tests.conftest import FIXTURES


def parsed():
    return parse_course(FIXTURES / "demo_gradebook.csv", FIXTURES / "demo_grade_history.csv", course_id="bb:T")


def test_header_parsing_marks_calculated_columns():
    cols = parse_gradebook_columns([
        "Homework 1 [Total Pts: 20 Score] |710001",
        "Final Grade [Total Pts: up to 980 Percentage] |730002",
        "Overall Grade [Total Pts: up to 980 Letter +/- 2] |730003",
        "Last Name",
    ])
    assert list(cols["ASSIGNMENT_ID"]) == ["bb:710001", "bb:730002", "bb:730003"]
    assert list(cols["CALCULATED"]) == [False, True, True]
    assert cols.iloc[0]["POINTS_POSSIBLE"] == 20


def test_sections_from_child_course_id():
    p = parsed()
    assert sorted(p["sections"]["SECTION_NAME"]) == ["M3A", "T2A", "T3A"]
    assert p["enrollments"]["SECTION_ID"].str.startswith("202620-DEMO-CS101-").all()


def test_due_dates_inferred_and_snapped_to_2359():
    p = parsed()
    a = p["assignments"].set_index("ASSIGNMENT_NAME")
    assert (a["DUE_SOURCE"] == "inferred_auto_zero").sum() == 13   # online HW 2-7 and Quiz 1-7
    assert a.loc["Quiz 1", "DUE_AT"] == pd.Timestamp("2026-12-23 23:59")
    assert a.loc["Homework 2", "DUE_AT"] == pd.Timestamp("2026-12-25 23:59")
    assert pd.isna(a.loc["Homework 1", "DUE_AT"])     # everyone submitted -> no auto zeros


def test_manifest_overrides_inference():
    m = pd.DataFrame({"ASSIGNMENT_NAME": ["Homework 1"], "DUE_AT": ["2026-12-20 23:59"]})
    p = parse_course(FIXTURES / "demo_gradebook.csv", FIXTURES / "demo_grade_history.csv", "bb:T", manifest=m)
    a = p["assignments"].set_index("ASSIGNMENT_NAME")
    assert a.loc["Homework 1", "DUE_SOURCE"] == "manifest"
    assert a.loc["Homework 1", "DUE_AT"] == pd.Timestamp("2026-12-20 23:59")


def test_needs_grading_and_future_items_excluded():
    rep = validation_report_dict(parsed())
    assert rep["needs_grading_excluded"] == 2
    assert "Final Project Capstone" in rep["items_not_yet_active"]
    assert rep["history_columns_not_in_gradebook"] == []


def test_points_match_blackboard_final_grade():
    p = parsed()
    perf = transform_data({"assignments": p["assignments"].drop(columns="DUE_SOURCE"),
                           "students": p["students"][["STUDENT_ID", "STUDENT_NAME"]],
                           "submissions": p["submissions"]})
    agg = perf.groupby("STUDENT_ID")[["POINTS_EARNED", "POINTS_POSSIBLE"]].sum()
    pct = 100 * agg.POINTS_EARNED / agg.POINTS_POSSIBLE
    bb = p["enrollments"].set_index("STUDENT_ID")["CURRENT_SCORE"]
    assert (pct - bb.loc[pct.index]).abs().max() < 0.01


def test_lateness_only_where_submit_time_exists():
    s = parsed()["submissions"]
    assert not s.loc[s["SUBMITTED_AT"].isna(), "LATE"].any()
