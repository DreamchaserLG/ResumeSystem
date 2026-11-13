"""Verify aggregates, calendar buckets and role boundaries against PostgreSQL."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import uploads
from app.core.db import owner_engine
from app.main import app
from test_v1_workflow import docx_bytes, login
from worker.tasks.parse import process_resume


def test_statistics_plans_events_and_scope(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    with TestClient(app) as client:
        hr = login(client, "hr.tech@company.internal", "hr123456")
        admin = login(client, "admin@company.internal", "admin123")
        other = login(client, "hr.market@company.internal", "dev123456")
        template = {"department_id":1, "status":"open", "rounds":[{"name":"技术面"}]}
        jobs = []
        apps = []
        try:
            for label,plan in (("满额",1),("缺口",4),("未配置",None)):
                resp = client.post("/api/v1/hr/jobs", headers=hr, json={
                    **template,"title":"统计测试"+label,"planned_headcount":plan,
                    "target_date":"2020-01-01",
                })
                assert resp.status_code == 201, resp.text
                jobs.append(resp.json()["id"])
            full, gap, unset = jobs
            data = docx_bytes(["基本信息","姓名：统计测试","教育经历","学校：清华大学","学历：硕士"])
            for job in (full,full,gap):
                res = client.post(f"/api/v1/applications?job_id={job}", headers=hr, files={
                    "file":("stat.docx",data,"application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                })
                assert res.status_code == 202, res.text
                aid = res.json()["application_id"]
                apps.append(aid)
                path = f"/api/v1/hr/applications/{aid}"
                info = client.get(path+"?include_evidence=false",headers=hr).json()
                transition = client.put(path+"/stage",headers=hr,json={"code":"hired","workflow_version":info["workflow_version"]})
                assert transition.status_code == 200, transition.text

            path = "/api/v1/statistics"
            assert client.get(path).status_code == 401
            assert client.get(path,headers=other,params={"job_id":gap}).status_code == 404
            assert client.get(path,headers=hr,params={"department_id":3}).status_code == 404
            assert client.get(path,headers=hr,params={"start":"2026-01-01"}).status_code == 422

            res = client.get(path,headers=hr,params={"days":"7","job_id":gap})
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["scope"] == "dept_hr" and body["accounts"] is None
            assert "admins" not in body["department_rows"][0]
            totals = body["totals"]
            assert (totals["planned"],totals["filled"],totals["gap"],totals["overdue"]) == (4,1,3,1)
            assert (totals["period_hired"],totals["new_applications"],totals["resumes"]) == (1,1,1)
            assert sum(p["hired"] for p in body["trend"]) == 1 and len(body["trend"]) == 7
            assert body["rounds"][0]["name"] == "技术面"
            assert "candidate_id" not in res.text and "统计测试\u0022" not in res.text

            scoped = client.get(path,headers=admin,params={"department_id":1}).json()
            rows = {row["id"]:row for row in scoped["jobs"]}
            assert (rows[full]["gap"],rows[full]["hired"],rows[full]["filled"]) == (0,2,1)
            assert rows[gap]["gap"] == 3 and rows[unset]["gap"] is None
            assert scoped["totals"]["gap"] == 3
            assert scoped["accounts"]["admins"] >= 1
            assert scoped["scope"] == "super_admin"

            prior = (datetime.now(ZoneInfo("Asia/Shanghai")).date()-timedelta(days=2)).isoformat()
            old = client.get(path,headers=hr,params={"start":prior,"end":prior,"job_id":gap}).json()
            assert old["totals"]["gap"] == 3 and old["totals"]["period_hired"] == 0
            candidate_path = f"/api/v1/hr/applications/{apps[-1]}"
            active = client.get(candidate_path+"?include_evidence=false",headers=hr).json()
            assert client.put(candidate_path+"/stage",headers=hr,json={
                "code":"review","workflow_version":active["workflow_version"],
            }).status_code == 200
            restored = client.get(path,headers=hr,params={"job_id":gap}).json()
            assert restored["totals"]["gap"] == 4 and restored["totals"]["period_hired"] == 1
            with owner_engine.begin() as db:
                db.execute(text("UPDATE audit_log SET created_at=now()-interval '8 days' WHERE target_id=:id AND action='recruitment_stage_change'"),
                           {"id":str(apps[-1])})
            assert client.get(path,headers=hr,params={"job_id":gap}).json()["totals"]["stalled"] == 1
            search = "/api/v1/hr/resume-cards/search"
            assert client.post(search,headers=hr,json={"stalled":True,"job_id":gap}).json()["total"] == 1
            assert client.post(search,headers=other,json={"stalled":True,"job_id":gap}).json()["total"] == 0
        finally:
            for aid in apps:
                assert client.post(f"/api/v1/hr/applications/{aid}/purge",headers=hr,json={"reason":"清理统计测试数据"}).status_code == 200
            if jobs:
                with owner_engine.begin() as db:
                    db.execute(text("DELETE FROM job WHERE id=ANY(:jobs)"), {"jobs":jobs})
