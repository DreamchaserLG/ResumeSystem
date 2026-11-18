"""鉴权依赖（02 §2 三层校验的 L1/L2 接入点）。

会话存 Redis（TTL=设置值）；鉴权时比对 DB 中 app_user.perm_version，
撤权/授权变更（perm_version++）后旧会话立即失效。
"""
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import json
import re
import time
from uuid import uuid4

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


SESSION_PREFIX = "rs:active-login:"
TAKEN_OVER = "该账号已在其他地方登录，请重新登录"
TOKEN_FORMAT = re.compile(r"([1-9][0-9]{0,18})\.[A-Za-z0-9_-]{43}")

# Only the active account record is written. The confirmation binds to the exact
# session the user was warned about, so it cannot invalidate a later login silently.
INSTALL_SESSION = """
local previous = redis.call('GET', KEYS[1])
local new_session = cjson.decode(ARGV[2])
if previous then
    local active = cjson.decode(previous)
    local previous_version = tonumber(active.perm_version)
    local current_version = tonumber(ARGV[4])
    if previous_version > current_version then
        return {'stale'}
    end
    if previous_version == current_version and active.login_id ~= ARGV[1] then
        return {'conflict', active.login_id}
    end
    if previous_version == current_version then
        new_session.previous_digest = active.token_digest
    end
end
redis.call('SET', KEYS[1], cjson.encode(new_session), 'EX', ARGV[3])
return {'ok'}
"""
REMOVE_SESSION = """
local raw = redis.call('GET', KEYS[1])
if raw and cjson.decode(raw).token_digest == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _session_key(user_id) -> str:
    return f"{SESSION_PREFIX}{user_id}"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user: dict, replace_login: str | None = None) -> tuple[str | None, dict | None, str | None]:
    """Returns a token and payload, or an opaque identifier for takeover confirmation."""
    token = f"{user['id']}.{new_session_token()}"
    payload = {
        "user_id": user["id"], "email": user["email"], "display_name": user["display_name"],
        "role": user["role"], "departments": user["departments"], "perm_version": user["perm_version"],
        "created_at": int(time.time()), "login_id": uuid4().hex, "token_digest": _digest(token),
    }
    result = get_redis().eval(INSTALL_SESSION, 1, _session_key(user["id"]),
                             replace_login or "", json.dumps(payload,ensure_ascii=False),
                             get_settings().session_ttl_seconds, user["perm_version"])
    if result[0] == "conflict":
        return None, None, result[1]
    if result[0] == "stale":
        raise HTTPException(409,"账号权限刚刚发生变化，请重新登录")
    return token, payload, None


def get_session(token: str) -> dict | None:
    key = TOKEN_FORMAT.fullmatch(token)
    if not key:
        return None
    raw = get_redis().get(_session_key(key[1]))
    if not raw:
        return None
    payload = json.loads(raw)
    digest = _digest(token)
    if not hmac.compare_digest(payload["token_digest"], digest):
        if hmac.compare_digest(payload.get("previous_digest",""), digest):
            raise HTTPException(401,TAKEN_OVER)
        return None
    return payload


def delete_session(token: str) -> None:
    key = TOKEN_FORMAT.fullmatch(token)
    if key:
        get_redis().eval(REMOVE_SESSION, 1, _session_key(key[1]), _digest(token))


def _fresh_account(user_id: int) -> tuple[int, str] | None:
    s = SessionLocal()
    try:
        row = s.execute(
            text("SELECT perm_version, status, display_name FROM app_user WHERE id = :i"),
            {"i": user_id},
        ).fetchone()
        if row is None or row[1] != "active":
            return None
        return int(row[0]), row[2]
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

    account = _fresh_account(sess["user_id"])
    if account is None:
        delete_session(token)
        raise HTTPException(status_code=401, detail="账号不可用")
    fresh, display_name = account
    if fresh != sess.get("perm_version"):
        # 权限版本变化：作废旧会话，强制重新登录（撤权立即生效）
        delete_session(token)
        raise HTTPException(status_code=401, detail="权限已变更，请重新登录")

    return AuthContext(
        user_id=sess["user_id"],
        email=sess["email"],
        display_name=display_name,
        role=sess["role"],
        perm_version=fresh,
        departments=[int(d) for d in sess.get("departments", [])],
    )


def require_super_admin(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    if auth.role != "super_admin":
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return auth


def require_recruitment_access(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Resolve the department scope for recruitment reads and writes."""
    if auth.role == "super_admin":
        with SessionLocal() as session:
            if not session.execute(text("SELECT 1 FROM system_admin WHERE user_id=:u"), {"u":auth.user_id}).first():
                raise HTTPException(status_code=403, detail="需要招聘管理权限")
            departments = session.execute(text("SELECT id FROM department ORDER BY id")).scalars().all()
        return replace(auth, departments=list(departments))
    if auth.role == "dept_hr" and auth.departments:
        return auth
    raise HTTPException(status_code=403, detail="需要招聘管理权限")


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
