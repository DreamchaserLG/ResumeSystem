"""Verify department-scoped onboarding and single-file import against real persistence."""
from io import BytesIO
from uuid import uuid4

from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import job_imports
from app.core.db import owner_engine
from app.main import app
from test_v1_workflow import login


def test_departments_and_job_draft(monkeypatch):
    monkeypatch.setattr(job_imports.parse_job_file, "delay", job_imports.parse_job_file.run)
    tag = uuid4().hex[:10]
    email, password = f"jobs-{tag}@company.internal", "integration-1234!"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    for idx, (label, value) in enumerate((("岗位名称", f"运维工程师 {tag}"), ("计划招聘人数", "2 人"))):
        table.cell(idx, 0).text = label
        table.cell(idx, 1).text = value
    document.add_paragraph("岗位职责：维护企业系统")
    document.add_paragraph("任职要求：熟悉生产环境")
    document.add_paragraph("必备技能：Linux、SQL")
    document.add_paragraph("工作经验要求：1–3年")
    document.add_paragraph("招聘开始日期：2026-11-18")
    document.add_paragraph("招聘截止日期：2026-12-18")
    stream = BytesIO()
    document.save(stream)
    with TestClient(app) as client:
        admin = login(client, "admin@company.internal", "admin123")
        other = login(client, "hr.market@company.internal", "dev123456")
        body = {"display_name": "演示部门账号", "email": email, "password": password}
        assert client.post("/api/v1/admin/users", headers=other, json={**body, "department_id": 1}).status_code == 403
        assert client.post("/api/v1/admin/users", headers=admin, json={**body, "department_id": 900000}).status_code == 404
        created = client.post("/api/v1/admin/users", headers=admin, json={**body, "department_id": 1})
        assert created.status_code == 200, created.text
        user_id = created.json()["user_id"]
        job_id = None
        try:
            accounts = client.get("/api/v1/admin/departments/1/accounts", headers=admin).json()["items"]
            assert {"id": user_id, "display_name": body["display_name"], "email": email, "status": "active"} in accounts
            assert client.get("/api/v1/admin/departments/1/accounts", headers=other).status_code == 403
            assert client.post("/api/v1/admin/users", headers=admin, json={**body, "department_id": 1}).status_code == 409
            hr = login(client, email, password)
            assert {d["id"] for d in client.get("/api/v1/jobs", headers=hr).json()["departments"]} == {1}
            path = "/api/v1/workspace/job-imports"
            upload = ("file", ("job.docx", stream.getvalue(), job_imports.MIME[".docx"]))
            assert client.post(path, headers=hr, files=[upload, upload]).status_code == 422
            task = client.post(path, headers=hr, files=[upload])
            assert task.status_code == 202, task.text
            result_path = path + "/" + task.json()["id"]
            assert client.get(result_path, headers=other).status_code == 404
            result = client.get(result_path, headers=hr).json()
            assert result["status"] == "ready", result
            draft = result["draft"]
            assert draft["title"] == f"运维工程师 {tag}"
            assert draft["jd"] == "岗位职责：维护企业系统"
            assert draft["planned_headcount"] == 2
            assert draft["department_name"] == ""
            assert draft["requirements"] == "熟悉生产环境" and draft["required_skills"] == ["Linux","SQL"]
            assert draft["experience_tags"] == ["1–3年"] and draft["recruitment_end"] == "2026-12-18"
            assert not any(row["title"] == draft["title"] for row in client.get("/api/v1/jobs", headers=hr).json()["items"])
            payload = {"title": draft["title"], "jd": draft["jd"], "planned_headcount": draft["planned_headcount"],
                       "department_id": 1, "status": "paused",
                       **{key:draft[key] for key in ("requirements","required_skills","experience_tags","recruitment_start","recruitment_end")}}
            assert client.post("/api/v1/hr/jobs", headers=hr, json={**payload,"recruitment_end":"2026-10-01"}).status_code==422
            assert client.post("/api/v1/hr/jobs", headers=hr, json={**payload, "department_id": 2}).status_code == 404
            saved = client.post("/api/v1/hr/jobs", headers=hr, json=payload)
            assert saved.status_code == 201, saved.text
            job_id = saved.json()["id"]
            summary = client.get(f"/api/v1/jobs/{job_id}/candidates",headers=hr).json()
            assert summary["job"]["required_skills"] == ["Linux","SQL"] and summary["total"] == 0
            assert client.get(f"/api/v1/jobs/{job_id}/candidates",headers=other).status_code==404
            changed = client.put(f"/api/v1/hr/jobs/{job_id}",headers=hr,
                json={"title":draft["title"],"jd":draft["jd"],"department_id":1,"status":"open"})
            assert changed.status_code == 200
            reread = client.get(f"/api/v1/jobs/{job_id}/candidates",headers=hr).json()["job"]
            assert reread["recruitment_start"]=="2026-11-18" and reread["requirements"]=="熟悉生产环境"
            assert client.put(f"/api/v1/hr/jobs/{job_id}", headers=admin, json={**payload, "department_id": 2}).status_code == 422
            assert not any(row["id"] == job_id for row in client.get("/api/v1/jobs", headers=other).json()["items"])
            multi_text = "岗位名称：岗位 A\n岗位名称：岗位 B".encode()
            task = client.post(path, headers=hr, files={"file": ("multi.txt", multi_text, "text/plain")}).json()
            error = client.get(path + "/" + task["id"], headers=hr).json()
            assert error["status"] == "failed" and "多个岗位" in error["error"]
        finally:
            with owner_engine.begin() as connection:
                if job_id:
                    connection.execute(text("DELETE FROM job WHERE id=:id"), {"id": job_id})
                connection.execute(text("DELETE FROM audit_log WHERE actor_id=:id OR (target_type='user' AND target_id=:target)"),
                                   {"id": user_id, "target": str(user_id)})
                connection.execute(text("DELETE FROM app_user WHERE id=:id"), {"id": user_id})
