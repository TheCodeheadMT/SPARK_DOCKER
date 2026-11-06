"""Local mode: no sign-in. Pick "Instructor mode" or "Admin mode" on the start page.

For running SPARK on your own computer or a private network while real Microsoft 365
sign-in is not set up. There are no passwords: anyone who can reach the server can choose
either mode, so it is refused when ENV=production.

  * Instructor mode: the Instructor role on every course (granted automatically, including
    courses added later), so nothing has to be assigned.
  * Admin mode: everything, including the Admin page.

Uses the same session cookie and User model as the other providers, so routes and
course-access checks are unchanged. Switch back with AUTH_MODE=simulated (test personas)
or, later, AUTH_MODE=entra (real Microsoft 365).
"""
from __future__ import annotations

from spark_api.auth.base import User

PERSONAS = {
    "instructor": {"key": "instructor", "oid": "00000000-0000-0000-0000-00000000local1", "name": "Instructor",
                   "preferred_username": "instructor@spark.local", "roles": ["Instructor"], "groups": [],
                   "description": "Your courses: roster, analytics, Course Director, Extra Instruction, uploads."},
    "admin": {"key": "admin", "oid": "00000000-0000-0000-0000-00000000local2", "name": "Administrator",
              "preferred_username": "admin@spark.local", "roles": ["Admin"], "groups": [],
              "description": "Everything in Instructor mode, plus the Admin page: admissions data, course "
                             "inventory, the Blackboard folder, and deleting any course."},
}


class LocalModeProvider:
    mode = "local"
    tenant_id = "local"

    def list_personas(self) -> list[dict]:
        return [{"key": p["key"], "name": p["name"], "upn": p["preferred_username"], "roles": p["roles"],
                 "description": p["description"]} for p in PERSONAS.values()]

    def user_for(self, key: str) -> User | None:
        p = PERSONAS.get(key)
        return User.from_claims({**p, "tid": self.tenant_id}, self.mode) if p else None

    def user_for_upn(self, upn: str) -> User | None:
        return next((self.user_for(k) for k, p in PERSONAS.items() if p["preferred_username"] == upn.lower()), None)

    def course_grants(self) -> list[tuple[str, str, str]]:
        """"*" = every course; expanded by the app when grants are seeded."""
        return [(PERSONAS["instructor"]["preferred_username"], "*", "Instructor")]
