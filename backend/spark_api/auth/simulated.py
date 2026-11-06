"""Simulated Microsoft 365 (Entra ID) provider.

Stands in for the real OIDC flow during development. A persona file supplies the
claims Microsoft would return; signing in as a persona issues the SAME session cookie
the real provider will issue after its OIDC callback.

Swap-out plan (Phase 6): add auth/entra.py implementing the same two calls
(login redirect + callback -> User) with MSAL; set AUTH_MODE=entra. Routes and the
course-access checks do not change.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from spark_api.auth.base import User


class SimulatedEntraProvider:
    mode = "simulated"

    def __init__(self, personas_file: Path):
        raw = yaml.safe_load(Path(personas_file).read_text())
        self.tenant_id = raw["tenant_id"]
        self.personas: dict[str, dict] = {p["key"]: p for p in raw["personas"]}

    def list_personas(self) -> list[dict]:
        return [{"key": p["key"], "name": p["name"], "upn": p["preferred_username"], "roles": p["roles"],
                 "description": p.get("description", "")} for p in self.personas.values()]

    def user_for(self, key: str) -> User | None:
        p = self.personas.get(key)
        if not p:
            return None
        return User.from_claims({**p, "tid": self.tenant_id}, self.mode)

    def user_for_upn(self, upn: str) -> User | None:
        for p in self.personas.values():
            if p["preferred_username"].lower() == upn.lower():
                return self.user_for(p["key"])
        return None

    def course_grants(self) -> list[tuple[str, str, str]]:
        """(upn, course_id, role) rows to seed CourseAccess from the persona file."""
        out = []
        for p in self.personas.values():
            role = next((r for r in ("CourseDirector", "Instructor") if r in p["roles"]), None)
            for cid in p.get("courses", []):
                if cid != "*" and role:
                    out.append((p["preferred_username"].lower(), cid, role))
        return out
