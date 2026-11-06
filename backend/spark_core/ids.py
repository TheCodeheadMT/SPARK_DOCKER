"""Course ID helpers.

Course IDs are namespaced strings so Blackboard and Canvas courses can coexist:
    bb:202620-DEMO-CS101      (Blackboard parent course folder name)
    canvas:12345                      (Canvas numeric course id)

Never cast a course ID to int. Use `slug()` whenever an ID becomes part of a file name.
"""
from __future__ import annotations

import re

LMS_PREFIXES = ("bb", "canvas", "fixture")


def make_course_id(lms: str, native_id: str | int) -> str:
    if lms not in LMS_PREFIXES:
        raise ValueError(f"unknown LMS '{lms}'")
    return f"{lms}:{native_id}"


def split_course_id(course_id: str) -> tuple[str, str]:
    lms, sep, native = course_id.partition(":")
    if not sep or lms not in LMS_PREFIXES or not native:
        raise ValueError(f"not a namespaced course id: {course_id!r}")
    return lms, native


def slug(course_id: str) -> str:
    """Filesystem-safe, reversible-enough name for per-course files."""
    split_course_id(course_id)  # validate
    return re.sub(r"[^A-Za-z0-9._-]+", "_", course_id.replace(":", "__"))
