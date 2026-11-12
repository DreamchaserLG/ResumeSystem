"""鉴权依赖（02 §2 三层校验的 L1/L2 接入点）。

会话存 Redis（TTL=设置值）；鉴权时比对 DB 中 app_user.perm_version，
撤权/授权变更（perm_version++）后旧会话立即失效。
"""
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request

from app.core.cache import get_redis
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import new_session_token
from sqlalchemy import text


@dataclass
class AuthContext:
    user_id: int
    email: str
    display_name: str
    role: str                      # super_admin | dept_hr
    perm_version: int
    departments: list[int] = field(default_factory=list)


SESSION_PREFIX = "rs:sess:"


def _session_key(token: str) -> str:
    return f"{SESSION_PREFIX}{token}"


def create_session(user: dict) -> tuple[str, dict]:
    """创建 Redis 会话，返回 (token, session_payload)。"""
    import json
    import time

    settings = get_settings()
    token = new_session_token()
    payload = {
        "user_id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user["role"],
        "departments": user["departments"],
        "perm_version": user["perm_version"],
        "created_at": int(time.time()),
    }
    r = get_redis()
    r.set(_session_key(token), json.dumps(payload, ensure_ascii=False), ex=settings.session_ttl_seconds)
    return token, payload


def get_session(token: str) -> dict | None:
    import json

    r = get_redis()
    raw = r.get(_session_key(token))
    return json.loads(raw) if raw else None


def delete_session(token: str) -> None:
    get_redis().delete(_session_key(token))


def _fresh_perm_version(user_id: int) -> int | None:
    s = SessionLocal()
    try:
        row = s.execute(
            text("SELECT perm_version, status FROM app_user WHERE id = :i"),
            {"i": user_id},
        ).fetchone()
        if row is None or row[1] != "active":
            return None
        return int(row[0])
    finally:
        s.close()


def get_token(request: Request) -> str:
    token = request.headers.get("X-Session-Token", "")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    return token


def require_auth(request: Request) -> AuthContext:
    """会话校验 + perm_version 比对 + 授权部门集合计算（02 §2.1：一次查询算出具体值）。"""
    token = get_token(request)
    sess = get_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")

    fresh = _fresh_perm_version(sess["user_id"])
    if fresh is None:
        delete_session(token)
        raise HTTPException(status_code=401, detail="账号不可用")
    if fresh != sess.get("perm_version"):
        # 权限版本变化：作废旧会话，强制重新登录（撤权立即生效）
        delete_session(token)
        raise HTTPException(status_code=401, detail="权限已变更，请重新登录")

    return AuthContext(
        user_id=sess["user_id"],
        email=sess["email"],
        display_name=sess["display_name"],
        role=sess["role"],
        perm_version=fresh,
        departments=[int(d) for d in sess.get("departments", [])],
    )


def require_super_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    if auth.role != "super_admin":
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return auth


def require_dept_hr(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    if auth.role != "dept_hr" or not auth.departments:
        raise HTTPException(status_code=403, detail="需要部门 HR 权限")
    return auth


def bump_perm_version(user_id: int) -> None:
    """授权/撤权时递增权限版本（02 §3），旧会话自然失效。

    系统级操作走 owner 连接：rs_app 上 app_user 的 UPDATE 策略要求超管上下文。
    """
    from app.core.db import owner_engine

    with owner_engine.begin() as cn:
        cn.execute(
            text("UPDATE app_user SET perm_version = perm_version + 1 WHERE id = :i"),
            {"i": user_id},
        )
