"""Identity model and session handling shared by every auth provider."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "spark_session"
APP_ROLES = ("Instructor", "CourseDirector", "Admin")


@dataclass
class User:
    """Built from Entra ID style claims. Routes depend only on this object."""
    oid: str
    upn: str                     # preferred_username
    name: str
    tenant_id: str
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    auth_mode: str = "simulated"

    @property
    def is_admin(self) -> bool:
        return "Admin" in self.roles

    @classmethod
    def from_claims(cls, claims: dict, auth_mode: str) -> "User":
        return cls(
            oid=claims["oid"], upn=claims["preferred_username"].lower(), name=claims.get("name", ""),
            tenant_id=claims.get("tid", ""), roles=[r for r in claims.get("roles", []) if r in APP_ROLES],
            groups=list(claims.get("groups", [])), auth_mode=auth_mode,
        )

    def to_claims(self) -> dict:
        return {"oid": self.oid, "preferred_username": self.upn, "name": self.name, "tid": self.tenant_id,
                "roles": self.roles, "groups": self.groups}

    def public(self) -> dict:
        d = asdict(self)
        d["is_admin"] = self.is_admin
        return d


class SessionCodec:
    """Signed, time-limited session cookie carrying the user's claims."""

    def __init__(self, secret_key: str, max_age_s: int):
        self._s = URLSafeTimedSerializer(secret_key, salt="spark-session")
        self.max_age_s = max_age_s

    def dumps(self, user: User) -> str:
        return self._s.dumps({"claims": user.to_claims(), "mode": user.auth_mode})

    def loads(self, token: str) -> User | None:
        try:
            data = self._s.loads(token, max_age=self.max_age_s)
        except (BadSignature, SignatureExpired):
            return None
        return User.from_claims(data["claims"], data.get("mode", "simulated"))
