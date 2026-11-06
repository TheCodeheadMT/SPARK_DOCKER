"""Regressions: people without a section are excluded; the current grade is found or computed."""
import re

import pandas as pd
import pytest

from adapters.bb_ultra.parser import _num, parse_course, read_bb_table, validation_report_dict
from tests.conftest import FIXTURES

GB = FIXTURES / "demo_gradebook.csv"
HIST = FIXTURES / "demo_grade_history.csv"


def write_variant(tmp_path, fn) -> str:
    df = read_bb_table(GB)
    df = fn(df)
    p = tmp_path / "gb.csv"
    df.to_csv(p, index=False)
    return str(p)


def parse(path, hist=HIST, **kw):
    return parse_course(path, hist, course_id="bb:X", **kw)


def test_numbers_with_percent_and_commas():
    assert _num("85.5%") == 85.5 and _num(" 1,234.5 ") == 1234.5 and _num("B+") is None and _num("-") is None


def test_rows_without_section_are_excluded_everywhere(tmp_path):
    def add_unsectioned(df):
        extra = df.iloc[:2].copy()
        extra["Username"] = ["ta.one", "preview_user"]
        extra["Child Course ID"] = ["", ""]
        return pd.concat([df, extra], ignore_index=True)
    p = parse(write_variant(tmp_path, add_unsectioned))
    assert "ALL" not in set(p["sections"]["SECTION_ID"]) and len(p["sections"]) == 3
    for key in ("students", "enrollments", "submissions"):
        assert not {"ta.one", "preview_user"} & set(p[key]["STUDENT_ID"]), key
    rep = validation_report_dict(p)
    assert rep["excluded_no_section"] == 2 and rep["students"] == 54


def test_course_without_any_sections_is_one_section(tmp_path):
    p = parse(write_variant(tmp_path, lambda df: df.drop(columns=["Child Course ID"])))
    assert list(p["sections"]["SECTION_NAME"]) == ["All students"] and len(p["students"]) == 54
    p = parse(write_variant(tmp_path, lambda df: df.assign(**{"Child Course ID": ""})))
    assert list(p["sections"]["SECTION_NAME"]) == ["All students"] and len(p["students"]) == 54


def test_current_grade_from_configured_column():
    p = parse(str(GB))
    rep = validation_report_dict(p)
    assert rep["current_grade_source"] == "configured_column" and rep["current_grade_column"] == "Final Grade"
    assert p["enrollments"]["CURRENT_SCORE"].notna().all()


def _drop_final(df):
    return df.drop(columns=[c for c in df.columns if c.startswith("Final Grade")])


def test_current_grade_computed_when_column_missing(tmp_path):
    """The many-section course had no 'Final Grade' column: every current grade was blank."""
    ref = parse(str(GB))["enrollments"].set_index("STUDENT_ID")["CURRENT_SCORE"]
    p = parse(write_variant(tmp_path, _drop_final))
    rep = validation_report_dict(p)
    assert rep["current_grade_source"] == "computed_points" and rep["current_grade_column"] is None
    got = p["enrollments"].set_index("STUDENT_ID")["CURRENT_SCORE"]
    assert got.notna().all()
    assert (got - ref.reindex(got.index)).abs().max() < 1.0          # matches Blackboard's own total


def test_current_grade_from_another_percentage_total(tmp_path):
    def rename(df):
        df = _drop_final(df)
        # Copy Prog Grade's values under an "Overall Total" percentage header, as another course might name it.
        prog = next(c for c in df.columns if c.startswith("Prog Grade"))
        df["Overall Total [Total Pts: up to 980 Percentage] |999999"] = df[prog].astype(str) + "%"
        return df
    rep = validation_report_dict(parse(write_variant(tmp_path, rename)))
    assert rep["current_grade_source"] == "percentage_column" and rep["current_grade_column"] == "Overall Total"


def test_letter_only_total_is_not_used_as_a_score(tmp_path):
    def lettered(df):
        df = _drop_final(df)
        df["Final Grade [Total Pts: up to 980 Letter] |123"] = "B+"
        return df
    p = parse(write_variant(tmp_path, lettered))
    assert validation_report_dict(p)["current_grade_source"] == "computed_points"
    assert p["enrollments"]["CURRENT_SCORE"].between(0, 100).all()


def test_rebuild_after_schema_change_drops_unsectioned(client, bb_share):
    from tests.conftest import BB_COURSE, as_user
    gb = bb_share / "SPARK" / "202620-DEMO-CS101" / "gradebook" / "gb.csv"
    text = gb.read_text()
    first = text.splitlines()[1]
    gb.write_text(text.rstrip("\n") + "\n" + re.sub(r",[^,]*-M3A,", ",,", first.replace("stu0001", "ta.one"), count=1) + "\n")
    run = client.post(f"/api/courses/{BB_COURSE}/ingest", headers=as_user("instructor")).json()["run"]
    assert run["report"]["excluded_no_section"] == 1
    roster = client.get(f"/api/courses/{BB_COURSE}/students", headers=as_user("instructor")).json()["students"]
    assert all(r["SECTION_NAME"] not in ("No Section", None) for r in roster)
    assert all(r["STUDENT_ID"] != "ta.one" for r in roster)
