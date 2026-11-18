"""Exercise administrator account lifecycle against real sessions and persistence."""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import owner_engine
from app.main import app
from conftest import sign_in


def test_admin_can_disable_enable_and_remove_accounts():
    login = "/api/v1/auth/login"
    me = "/api/v1/auth/me"
    directory = "/api/v1/admin/organization"
    with TestClient(app) as client:
        admin = sign_in(client,{"email":"admin@company.internal","password":"admin123"})
        admin.raise_for_status()
        admin_id = admin.json()["session"]["user_id"]
        headers = {"X-Session-Token":admin.json()["token"]}
        body = {"email":f"lifecycle-{uuid4().hex[:10]}@company.internal","password":"verify-1234!",
                "display_name":"账号生命周期验证","department_id":1}
        created = client.post("/api/v1/admin/users",json=body,headers=headers)
        created.raise_for_status()
        user_id = created.json()["user_id"]
        path = f"/api/v1/admin/users/{user_id}"
        credentials = {key:body[key] for key in ("email","password")}
        hr = sign_in(client,credentials)
        hr.raise_for_status()
        old_token = {"X-Session-Token":hr.json()["token"]}
        assert client.put(path+"/status",json={"status":"disabled"},headers=old_token).status_code==403
        assert client.delete(path,headers=old_token).status_code==403
        assert client.delete(f"/api/v1/admin/users/{admin_id}",headers=headers).status_code==409
        assert client.put(f"/api/v1/admin/users/{admin_id}/status",json={"status":"disabled"},headers=headers).status_code==409
        assert client.put(path+"/status",json={"status":"disabled"},headers=headers).json()["status"]=="disabled"
        assert client.post(login,json=credentials).status_code==401
        listing = client.get(directory,headers=headers).json()["users"]
        assert next(row for row in listing if row["id"]==user_id)["status"]=="disabled"
        assert client.put(path+"/status",json={"status":"active"},headers=headers).json()["status"]=="active"
        assert client.get(me,headers=old_token).status_code==401
        active = sign_in(client,credentials)
        active.raise_for_status()
        active_token = {"X-Session-Token":active.json()["token"]}
        profile = client.post("/api/v1/auth/profile",headers=active_token,data={"display_name":body["display_name"],"office":"暂存资料"})
        profile.raise_for_status()
        assert client.delete(path,headers=headers).json()["ok"]
        assert client.get(me,headers=active_token).status_code==401
        assert client.post(login,json=credentials).status_code==401
        assert client.put(path+"/status",json={"status":"active"},headers=headers).status_code==404
        assert client.post("/api/v1/admin/grant-dept-hr",json={"user_id":user_id,"department_id":1},headers=headers).status_code==404
        assert user_id not in {item["id"] for item in client.get(directory,headers=headers).json()["users"]}
        with owner_engine.connect() as db:
            params={"id":user_id}
            saved=db.execute(text("SELECT status,password_hash FROM app_user WHERE id=:id"),params).one()
            assert tuple(saved)==("deleted","")
            assert not db.execute(text("SELECT 1 FROM user_profile WHERE user_id=:id"),params).first()
            assert not db.execute(text("SELECT 1 FROM department_admin WHERE user_id=:id"),params).first()
            assert db.execute(text("SELECT 1 FROM audit_log WHERE actor_id=:id AND action='login'"),params).first()
            assert db.execute(text("SELECT 1 FROM audit_log WHERE action='user_delete' AND target_id=:target"),
                              {"target":str(user_id)}).first()
        invited=client.post("/api/v1/admin/users",json=body,headers=headers)
        invited.raise_for_status()
        assert invited.json()["user_id"]!=user_id
        client.delete(f"/api/v1/admin/users/{invited.json()['user_id']}",headers=headers).raise_for_status()
