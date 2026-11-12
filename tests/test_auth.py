"""M2 权限模块测试：登录、角色守卫、撤权失效、审计落库。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

HR = {"email": "hr.tech@company.internal", "password": "hr123456"}
ADMIN = {"email": "admin@company.internal", "password": "admin123"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(c: TestClient, cred: dict) -> str:
    r = c.post("/api/v1/auth/login", json=cred)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_login_success_returns_real_role(client):
    r = client.post("/api/v1/auth/login", json=HR)
    body = r.json()
    assert body["session"]["role"] == "dept_hr"
    assert body["session"]["departments"] == [1]
    assert body["token"]


def test_login_wrong_password_denied(client):
    r = client.post(
        "/api/v1/auth/login", json={"email": HR["email"], "password": "bad-pass"}
    )
    assert r.status_code == 401


def test_login_unknown_email_denied(client):
    r = client.post(
        "/api/v1/auth/login", json={"email": "nobody@x.internal", "password": "whatever1"}
    )
    assert r.status_code == 401  # 与密码错误同响应，防枚举


def test_jobs_scoped_to_department(client):
    token = _login(client, HR)
    r = client.get("/api/v1/jobs", headers={"X-Session-Token": token})
    items = r.json()["items"]
    assert items and all(i["department_id"] == 1 for i in items)


def test_hr_cannot_read_audit(client):
    token = _login(client, HR)
    r = client.get("/api/v1/admin/audit", headers={"X-Session-Token": token})
    assert r.status_code == 403


def test_admin_can_read_audit(client):
    token = _login(client, ADMIN)
    r = client.get("/api/v1/admin/audit", headers={"X-Session-Token": token})
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_perm_version_bump_invalidates_session(client):
    """撤权（perm_version++）后旧会话立即失效（02 §3）。"""
    from app.core.auth import bump_perm_version
    from app.core.db import SessionLocal
    from sqlalchemy import text

    token = _login(client, HR)
    s = SessionLocal()
    try:
        uid = s.execute(
            text("SELECT id FROM app_user WHERE email = :e"), {"e": HR["email"]}
        ).scalar_one()
    finally:
        s.close()
    bump_perm_version(uid)
    r = client.get("/api/v1/jobs", headers={"X-Session-Token": token})
    assert r.status_code == 401


def test_audit_log_written_on_fail(client):
    client.post("/api/v1/auth/login", json={"email": HR["email"], "password": "nope-nope"})
    from app.core.db import owner_engine
    from sqlalchemy import text

    # 审计回读走 owner 连接：audit_log 的 SELECT 策略仅超管可见（rs_app 无上下文应不可见）
    with owner_engine.connect() as cn:
        n = cn.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE action='auth_fail' AND target_id = :e"
            ),
            {"e": HR["email"]},
        ).scalar_one()
    assert n >= 1

    # 反向断言：无上下文的 rs_app 连接看不到审计（audit_log RLS 生效）
    from app.core.db import SessionLocal

    s = SessionLocal()
    try:
        hidden = s.execute(
            text("SELECT count(*) FROM audit_log WHERE action='auth_fail'")
        ).scalar_one()
    finally:
        s.close()
    assert hidden == 0
