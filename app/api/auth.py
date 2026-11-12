"""认证路由：登录 / 登出 / 当前会话。

登录即权限验证：邮箱 + 密码 → PBKDF2 校验 → 角色与部门集合由授权表算出（02 §1）。
失败（密码错误/账号禁用）写 auth_fail 审计并统一返回 401，不区分具体原因（防枚举）。
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.core.audit import write_audit
from app.core.auth import (
    AuthContext,
    create_session,
    delete_session,
    require_auth,
)
from app.core.db import SessionLocal
from app.core.security import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class SessionOut(BaseModel):
    user_id: int
    display_name: str
    email: str
    role: str
    departments: list[int]
    display_departments: list[str]


@router.post("/login")
def login(body: LoginIn):
    s = SessionLocal()
    try:
        row = s.execute(
            text(
                "SELECT id, display_name, email, password_hash, status, perm_version"
                " FROM app_user WHERE email = :e"
            ),
            {"e": body.email.strip().lower()},
        ).fetchone()
    finally:
        s.close()

    if row is None or row.status != "active" or not verify_password(
        body.password, row.password_hash
    ):
        write_audit(
            actor_id=row.id if row else None,
            action="auth_fail",
            target_type="user",
            target_id=body.email[:64],
            result="denied",
        )
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    # 角色与数据范围：超管不接触简历正文（02 §1）；部门 HR 计算授权部门集合
    role = "super_admin" if _is_super_admin(row.id) else "dept_hr"
    departments = _departments_of(row.id)
    if role == "dept_hr" and not departments:
        write_audit(row.id, "auth_fail", "user", row.email, "no_scope")
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="账号尚未授权任何部门，请联系管理员")

    user = {
        "id": row.id,
        "email": row.email,
        "display_name": row.display_name,
        "role": role,
        "departments": departments,
        "perm_version": int(row.perm_version),
    }
    token, payload = create_session(user)
    write_audit(row.id, "login", "user", row.email, detail={"role": role})

    return {
        "token": token,
        "session": {
            "user_id": payload["user_id"],
            "display_name": payload["display_name"],
            "email": payload["email"],
            "role": payload["role"],
            "departments": payload["departments"],
            "display_departments": _department_names(departments),
        },
    }


@router.post("/logout")
def logout(request: Request, auth: AuthContext = Depends(require_auth)):
    token = request.headers.get("X-Session-Token", "")
    delete_session(token)
    write_audit(auth.user_id, "logout", "user", auth.email)
    return {"ok": True}


@router.get("/me")
def me(auth: AuthContext = Depends(require_auth)):
    return {
        "user_id": auth.user_id,
        "display_name": auth.display_name,
        "email": auth.email,
        "role": auth.role,
        "departments": auth.departments,
        "display_departments": _department_names(auth.departments),
    }


def _is_super_admin(user_id: int) -> bool:
    s = SessionLocal()
    try:
        return (
            s.execute(
                text("SELECT 1 FROM system_admin WHERE user_id = :i"), {"i": user_id}
            ).fetchone()
            is not None
        )
    finally:
        s.close()


def _departments_of(user_id: int) -> list[int]:
    s = SessionLocal()
    try:
        rows = s.execute(
            text("SELECT department_id FROM department_admin WHERE user_id = :i"),
            {"i": user_id},
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        s.close()


def _department_names(ids: list[int]) -> list[str]:
    if not ids:
        return []
    s = SessionLocal()
    try:
        rows = s.execute(
            text("SELECT name FROM department WHERE id = ANY(:ids)"),
            {"ids": ids},
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        s.close()
