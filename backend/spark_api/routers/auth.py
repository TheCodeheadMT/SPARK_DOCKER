from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from spark_api.auth.base import SESSION_COOKIE, User
from spark_api.deps import current_user

router = APIRouter()


class SimLogin(BaseModel):
    persona: str


@router.get("/auth/config")
def auth_config(request: Request):
    st = request.app.state
    out = {"mode": st.settings.auth_mode, "env": st.settings.env}
    if st.settings.auth_mode in ("simulated", "local"):
        out["personas"] = st.auth.list_personas()
    return out


@router.post("/auth/sim/login")
def sim_login(body: SimLogin, request: Request, response: Response):
    st = request.app.state
    if st.settings.auth_mode not in ("simulated", "local"):
        raise HTTPException(status_code=404)
    user = st.auth.user_for(body.persona)
    if not user:
        raise HTTPException(status_code=400, detail="Unknown persona")
    response.set_cookie(
        SESSION_COOKIE, st.sessions.dumps(user), max_age=st.settings.session_max_age_s,
        httponly=True, samesite="lax", secure=st.settings.is_production, path="/",
    )
    return {"user": user.public()}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"user": user.public()}
