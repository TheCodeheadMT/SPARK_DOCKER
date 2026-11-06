"""Synthetic course generator: realistic-shaped data with no real students.

Used for development, demos and load tests. Deterministic for a given seed.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from adapters.base import CourseData, CourseRef
from spark_core.ids import make_course_id


class FixtureAdapter:
    lms = "fixture"

    def __init__(self, n_courses: int = 2, n_students: int = 60, seed: int = 7):
        self.n_courses, self.n_students, self.seed = n_courses, n_students, seed

    def list_courses(self) -> list[CourseRef]:
        return [CourseRef(make_course_id("fixture", f"DEMO{i + 1:03d}"), f"Demo Course {i + 1}", "fixture", "synthetic")
                for i in range(self.n_courses)]

    def source_version(self, course: CourseRef) -> str:
        return f"seed{self.seed}"

    def load_course(self, course: CourseRef) -> CourseData:
        rng = np.random.default_rng(int(hashlib.sha256(f'{self.seed}:{course.course_id}'.encode()).hexdigest()[:8], 16))
        cid = course.course_id
        secs = ["A1", "B2", "C3"]
        sections = pd.DataFrame({"COURSE_ID": cid, "SECTION_ID": [f"{cid}-{s}" for s in secs], "SECTION_NAME": secs})

        start = pd.Timestamp("2027-01-11 23:59")
        items = []
        for w in range(10):
            items.append(("Homework", f"Homework {w + 1}", 20, start + pd.Timedelta(days=7 * w + 2), True))
            items.append(("Quiz", f"Quiz {w + 1}", 30, start + pd.Timedelta(days=7 * w + 4), False))
        items += [("Exam", "Graded Review 1", 100, start + pd.Timedelta(days=24), False),
                  ("Exam", "Graded Review 2", 100, start + pd.Timedelta(days=52), False)]
        assignments = pd.DataFrame([{
            "ASSIGNMENT_ID": f"fx:{i}", "ASSIGNMENT_NAME": n, "DUE_AT": d, "POINTS_POSSIBLE": float(p),
            "ASSIGNMENT_GROUP_ID": g, "DUE_SOURCE": "lms", "_online": online} for i, (g, n, p, d, online) in enumerate(items)])

        rows_s, rows_e, rows_sub = [], [], []
        for k in range(self.n_students):
            sid = f"demo{k + 1:04d}"
            ability = rng.beta(6, 2)
            diligence = rng.beta(5, 2)
            sec = secs[k % 3]
            rows_s.append({"STUDENT_ID": sid, "STUDENT_NAME": f"Demo Student{k + 1:04d}",
                           "STUDENT_SORTABLE_NAME": f"Student{k + 1:04d}, Demo", "LOGIN_ID": sid, "SIS_USER_ID": None})
            for _, a in assignments.iterrows():
                if rng.random() > 0.85 + 0.15 * diligence:
                    pts, sub_at = 0.0, None
                else:
                    pts = float(np.clip(rng.normal(ability, 0.12), 0, 1) * a["POINTS_POSSIBLE"]).__round__(1)
                    sub_at = (a["DUE_AT"] - pd.Timedelta(hours=float(rng.normal(30 * diligence, 24))) if a["_online"] else None)
                rows_sub.append({"STUDENT_ID": sid, "ASSIGNMENT_ID": a["ASSIGNMENT_ID"], "POINTS_EARNED": pts,
                                 "SUBMITTED_AT": sub_at, "GRADED_AT": a["DUE_AT"] + pd.Timedelta(days=2),
                                 "LATE": bool(sub_at is not None and sub_at > a["DUE_AT"])})
            rows_e.append({"COURSE_ID": cid, "ENROLLMENT_ID": f"{cid}:{sid}", "SECTION_ID": f"{cid}-{sec}",
                           "STUDENT_ID": sid, "ENROLLMENT_TYPE": "StudentEnrollment", "ENROLLMENT_STATE": "active",
                           "UPDATED_AT": pd.Timestamp("2027-03-01"), "CURRENT_SCORE": None, "CURRENT_GRADE": None,
                           "CURRENT_POINTS": None, **{k2: rows_s[-1][k2] for k2 in
                                                       ("STUDENT_NAME", "STUDENT_SORTABLE_NAME", "LOGIN_ID", "SIS_USER_ID")}})
        subs = pd.DataFrame(rows_sub)
        subs["SUBMITTED_AT"] = pd.to_datetime(subs["SUBMITTED_AT"])
        enroll = pd.DataFrame(rows_e)
        totals = subs.merge(assignments[["ASSIGNMENT_ID", "POINTS_POSSIBLE"]]).groupby("STUDENT_ID")[
            ["POINTS_EARNED", "POINTS_POSSIBLE"]].sum()
        enroll["CURRENT_SCORE"] = enroll["STUDENT_ID"].map((100 * totals.POINTS_EARNED / totals.POINTS_POSSIBLE).round(2))
        return CourseData(sections, enroll, pd.DataFrame(rows_s), assignments.drop(columns="_online"), subs,
                          self.source_version(course), {"students": self.n_students, "synthetic": True})
