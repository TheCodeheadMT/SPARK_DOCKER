"""Canvas adapter: the original SPARK Canvas client + extractors, without Streamlit.

Ported from canvas_client.py and extract_canvas.py. API calls and field mappings are
unchanged; differences:
  * Course IDs are namespaced ("canvas:12345") and enrollment/section IDs are kept as
    strings so they can share tables with Blackboard data.
  * The token is a server-side setting (CANVAS_API_TOKEN) instead of a per-session
    paste. Per-user Canvas tokens can come back later via the metadata DB if needed.
  * Submissions are pulled per student as before; the ingest worker runs this off the
    request path.

NOTE: not yet exercised against a live Canvas instance in this repo (no token here).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd
import requests

from adapters.base import CourseData, CourseRef
from spark_core.ids import make_course_id, split_course_id

DEFAULT_ENROLLMENT_STATES = ["active", "invited", "creation_pending", "inactive", "completed"]


class CanvasClient:
    def __init__(self, base_url: str, api_token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_token}"})
        self.timeout = timeout

    def _paginate_json(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        next_url, next_params = url, (params or {})
        while next_url:
            resp = self.session.get(next_url, params=next_params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data if isinstance(data, list) else [data])
            next_url = resp.links.get("next", {}).get("url")
            next_params = None
        return results

    def get_courses(self):
        return self._paginate_json(f"{self.base_url}/api/v1/courses", {"enrollment_type": "teacher", "per_page": 100})

    def get_sections(self, course_id: str):
        return self._paginate_json(f"{self.base_url}/api/v1/courses/{course_id}/sections", {"per_page": 100})

    def get_course_student_enrollments(self, course_id: str):
        return self._paginate_json(f"{self.base_url}/api/v1/courses/{course_id}/enrollments", {
            "per_page": 100, "type[]": ["StudentEnrollment"], "state[]": DEFAULT_ENROLLMENT_STATES,
            "include[]": ["user", "current_points"]})

    def get_assignments(self, course_id: str):
        return self._paginate_json(f"{self.base_url}/api/v1/courses/{course_id}/assignments",
                                   {"per_page": 100, "include[]": ["all_dates", "overrides"]})

    def get_assignment_groups(self, course_id: str):
        return self._paginate_json(f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups", {"per_page": 100})

    def get_student_submissions(self, course_id: str, user_id: int):
        return self._paginate_json(f"{self.base_url}/api/v1/courses/{course_id}/students/submissions",
                                   {"student_ids[]": user_id, "per_page": 100, "include[]": "submission_history"})


# --- extractors (from extract_canvas.py) -------------------------------------------
def extract_assignments(assignments: list, groups: dict) -> pd.DataFrame:
    if not assignments:
        return pd.DataFrame(columns=["ASSIGNMENT_ID", "ASSIGNMENT_NAME", "DUE_AT", "POINTS_POSSIBLE",
                                     "ASSIGNMENT_GROUP_ID", "DUE_SOURCE"])
    df = pd.DataFrame(assignments)
    out = df[["id", "name", "due_at", "points_possible", "assignment_group_id"]].rename(columns={
        "id": "ASSIGNMENT_ID", "name": "ASSIGNMENT_NAME", "due_at": "DUE_AT",
        "points_possible": "POINTS_POSSIBLE", "assignment_group_id": "ASSIGNMENT_GROUP_ID"})
    out["ASSIGNMENT_ID"] = "canvas:" + out["ASSIGNMENT_ID"].astype(str)
    out["ASSIGNMENT_GROUP_ID"] = out["ASSIGNMENT_GROUP_ID"].map(lambda g: groups.get(g, str(g)))
    out["DUE_AT"] = pd.to_datetime(out["DUE_AT"], errors="coerce", utc=True)
    out["DUE_SOURCE"] = out["DUE_AT"].notna().map({True: "lms", False: "none"})
    return out


def extract_submissions(submissions: list) -> pd.DataFrame:
    if not submissions:
        return pd.DataFrame(columns=["STUDENT_ID", "ASSIGNMENT_ID", "POINTS_EARNED", "SUBMITTED_AT", "GRADED_AT", "LATE"])
    df = pd.DataFrame(submissions)
    out = df[["user_id", "assignment_id", "score", "submitted_at", "graded_at", "late"]].rename(columns={
        "user_id": "STUDENT_ID", "assignment_id": "ASSIGNMENT_ID", "score": "POINTS_EARNED",
        "submitted_at": "SUBMITTED_AT", "graded_at": "GRADED_AT", "late": "LATE"})
    out["STUDENT_ID"] = out["STUDENT_ID"].astype(str)
    out["ASSIGNMENT_ID"] = "canvas:" + out["ASSIGNMENT_ID"].astype(str)
    for c in ("SUBMITTED_AT", "GRADED_AT"):
        out[c] = pd.to_datetime(out[c], errors="coerce", utc=True)
    return out


def extract_sections(sections: list, course_id: str) -> pd.DataFrame:
    df = pd.DataFrame(sections or [], columns=["id", "name"])
    out = df[["id", "name"]].rename(columns={"id": "SECTION_ID", "name": "SECTION_NAME"})
    out["SECTION_ID"] = out["SECTION_ID"].astype(str)
    out.insert(0, "COURSE_ID", course_id)
    return out


def extract_enrollments(enrollments: list, course_id: str) -> pd.DataFrame:
    rows = []
    for e in enrollments or []:
        u, g = e.get("user") or {}, e.get("grades") or {}
        rows.append({
            "COURSE_ID": course_id,
            "ENROLLMENT_ID": str(e.get("id")),
            "SECTION_ID": str(e.get("course_section_id") or e.get("section_id") or "No Section"),
            "STUDENT_ID": str(u.get("id") or e.get("user_id")),
            "ENROLLMENT_TYPE": e.get("type") or e.get("role"),
            "ENROLLMENT_STATE": e.get("enrollment_state") or e.get("workflow_state"),
            "UPDATED_AT": pd.to_datetime(e.get("updated_at"), errors="coerce", utc=True),
            "CURRENT_SCORE": g.get("current_score"),
            "CURRENT_GRADE": g.get("current_grade"),
            "CURRENT_POINTS": g.get("current_points"),
            "STUDENT_NAME": u.get("name"),
            "STUDENT_SORTABLE_NAME": u.get("sortable_name"),
            "LOGIN_ID": u.get("login_id"),
            "SIS_USER_ID": u.get("sis_user_id"),
        })
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["ENROLLMENT_ID"]) if not df.empty else df


class CanvasAdapter:
    lms = "canvas"

    def __init__(self, base_url: str, api_token: str):
        self.client = CanvasClient(base_url, api_token)
        self.base_url = base_url

    def list_courses(self) -> list[CourseRef]:
        return [CourseRef(make_course_id("canvas", c["id"]), c.get("name", str(c["id"])), "canvas", self.base_url)
                for c in self.client.get_courses()]

    def source_version(self, course: CourseRef) -> str:
        # Canvas is live; there is no cheap content hash. Version = ingest time bucket.
        return "live"

    def load_course(self, course: CourseRef) -> CourseData:
        _, native = split_course_id(course.course_id)
        groups = {g["id"]: g["name"] for g in self.client.get_assignment_groups(native)}
        sections = extract_sections(self.client.get_sections(native), course.course_id)
        enroll = extract_enrollments(self.client.get_course_student_enrollments(native), course.course_id)
        students = (enroll[["STUDENT_ID", "STUDENT_NAME", "STUDENT_SORTABLE_NAME", "LOGIN_ID", "SIS_USER_ID"]]
                    .dropna(subset=["STUDENT_ID"]).drop_duplicates("STUDENT_ID"))
        assignments = extract_assignments(self.client.get_assignments(native), groups)
        subs = pd.concat([extract_submissions(self.client.get_student_submissions(native, sid))
                          for sid in students["STUDENT_ID"]], ignore_index=True) if len(students) else \
            extract_submissions([])
        version = hashlib.sha256(json.dumps([len(subs), str(subs["GRADED_AT"].max())]).encode()).hexdigest()[:16]
        return CourseData(sections, enroll, students, assignments, subs, version,
                          {"students": int(len(students)), "graded_cells": int(len(subs))})
