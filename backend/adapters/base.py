"""LMS adapter interface.

Every LMS (Blackboard files, Canvas API, synthetic fixtures) produces the same
CourseData frames, in the column shapes the original Canvas extractors produced.
Everything downstream (transform_data, features, models, plots) is LMS-neutral.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class CourseRef:
    course_id: str          # namespaced, e.g. "bb:202620-DEMO-CS101"
    name: str
    lms: str                # "bb" | "canvas" | "fixture"
    source: str = ""        # folder path, API base URL, ...


@dataclass
class CourseData:
    sections: pd.DataFrame      # COURSE_ID, SECTION_ID, SECTION_NAME
    enrollments: pd.DataFrame   # FACT_ENROLLMENTS columns
    students: pd.DataFrame      # STUDENT_ID, STUDENT_NAME, STUDENT_SORTABLE_NAME, LOGIN_ID, SIS_USER_ID
    assignments: pd.DataFrame   # ASSIGNMENT_ID, ASSIGNMENT_NAME, DUE_AT, POINTS_POSSIBLE, ASSIGNMENT_GROUP_ID, DUE_SOURCE
    submissions: pd.DataFrame   # STUDENT_ID, ASSIGNMENT_ID, POINTS_EARNED, SUBMITTED_AT, GRADED_AT, LATE
    source_version: str         # hash of the inputs; same inputs -> same version
    report: dict = field(default_factory=dict)   # validation notes shown in the UI
    # Optional, for the assignment timeline: STUDENT_ID, ASSIGNMENT_ID, FIRST_ATTEMPT_AT, LAST_ATTEMPT_AT, ATTEMPTS.
    # assignments may also carry ASSIGNED_AT and ASSIGNED_SOURCE. Adapters without them leave these out.
    attempts: pd.DataFrame | None = None


ATTEMPT_COLUMNS = ["STUDENT_ID", "ASSIGNMENT_ID", "FIRST_ATTEMPT_AT", "LAST_ATTEMPT_AT", "ATTEMPTS"]


def empty_attempts() -> pd.DataFrame:
    """CourseData.attempts with no rows, typed so DuckDB gets the right column types."""
    return pd.DataFrame({
        "STUDENT_ID": pd.Series(dtype="string"), "ASSIGNMENT_ID": pd.Series(dtype="string"),
        "FIRST_ATTEMPT_AT": pd.Series(dtype="datetime64[ns]"), "LAST_ATTEMPT_AT": pd.Series(dtype="datetime64[ns]"),
        "ATTEMPTS": pd.Series(dtype="Int64"),
    })


class LMSAdapter(Protocol):
    lms: str

    def list_courses(self) -> list[CourseRef]: ...

    def source_version(self, course: CourseRef) -> str: ...

    def load_course(self, course: CourseRef) -> CourseData: ...
