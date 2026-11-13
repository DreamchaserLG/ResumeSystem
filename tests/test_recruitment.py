"""Job configuration, single-stage recruitment, and scoped transfers."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import uploads
from app.core.db import owner_engine
from app.main import app
from test_v1_workflow import docx_bytes, login
from worker.tasks.parse import process_resume


def test_job_rounds_and_same_department_transfer(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    with TestClient(app) as client:
        hr = login(client, "hr.tech@company.internal", "hr123456")
        outside = login(client, "hr.market@company.internal", "dev123456")
        admin = login(client, "admin@company.internal", "admin123")
        settings = {"title":"招聘流程测试岗位","department_id":1,"rounds":[{"name":"技术面"},{"name":"终面"}]}
        result = client.post("/api/v1/hr/jobs", headers=hr, json=settings)
        assert result.status_code == 201, result.text
        source = result.json()["id"]
        target = client.post("/api/v1/hr/jobs", headers=hr, json={
            **settings,"title":"调剂测试岗位","rounds":[{"name":"业务面"}],
        }).json()["id"]
        assert client.post("/api/v1/hr/jobs", headers=hr, json={
            **settings,"rounds":[{"name":"待审核"}],
        }).status_code == 422

        def jobs(headers):
            return {job["id"]:job for job in client.get("/api/v1/jobs", headers=headers).json()["items"]}

        source_rounds = jobs(hr)[source]["rounds"]
        target_round = jobs(hr)[target]["rounds"][0]["id"]
        data = docx_bytes(["基本信息", "姓名：流程测试", "工作经历", "公司：示例科技有限公司", "使用 Go 开发"])
        result = client.post(f"/api/v1/applications?job_id={source}", headers=hr, files={
            "file":("synthetic.docx",data,"application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        })
        assert result.status_code == 202, result.text
        app_id = result.json()["application_id"]
        base = f"/api/v1/hr/applications/{app_id}"
        assert jobs(hr)[source]["application_count"] == jobs(admin)[source]["application_count"] == 1
        assert source not in jobs(outside)
        assert client.get(base+"/transfer-options", headers=outside).status_code == 404
        options = client.get(base+"/transfer-options", headers=hr).json()["items"]
        assert target in {item["id"] for item in options} and source not in {item["id"] for item in options}

        detail = client.get(base+"?include_evidence=false", headers=hr).json()
        assert detail["stage"]["label"] == "待审核" and detail["evidence"] == []
        start = {"code":"interview","round_id":source_rounds[0]["id"],"workflow_version":detail["workflow_version"]}
        assert client.put(base+"/stage",headers=hr,json={**start,"round_id":target_round}).status_code == 422
        change = client.put(base+"/stage",headers=hr,json=start)
        assert change.status_code == 200, change.text
        assert change.json()["stage"]["label"] == "技术面"
        assert client.put(base+"/stage",headers=hr,json=start).status_code == 409
        assert client.put(f"/api/v1/hr/jobs/{source}",headers=hr,json={**settings,"rounds":[]}).status_code == 409

        cards = client.post("/api/v1/hr/resume-cards/search",headers=hr,json={
            "job_id":source,"stage_key":f"interview:{source_rounds[0]['id']}",
        })
        assert cards.status_code == 200, cards.text
        assert cards.json()["items"][0]["stage"]["label"] == "技术面"
        expected = change.json()["workflow_version"]
        update = client.put(f"/api/v1/hr/jobs/{source}",headers=hr,json={**settings,"rounds":[
            {"id":source_rounds[1]["id"],"name":"终面"},{"id":source_rounds[0]["id"],"name":"专业面"},
        ]})
        assert update.status_code == 200, update.text
        assert [r["name"] for r in jobs(hr)[source]["rounds"]] == ["终面","专业面"]
        assert client.get(base,headers=hr).json()["stage"]["label"] == "专业面"
        hired = client.put(base+"/stage",headers=hr,json={"code":"hired","workflow_version":expected})
        assert hired.status_code == 200 and hired.json()["stage"]["label"] == "录取"
        rejected = client.put(base+"/stage",headers=hr,json={"code":"rejected","workflow_version":hired.json()["workflow_version"]})
        assert rejected.status_code == 200 and rejected.json()["stage"]["label"] == "淘汰"

        move = client.post(base+"/transfer",headers=hr,json={
            "job_id":target,"workflow_version":rejected.json()["workflow_version"],"reason":"匹配另一个岗位",
        })
        assert move.status_code == 200, move.text
        assert move.json()["remains_visible"] and move.json()["stage"]["label"] == "待审核"
        moved = client.get(base,headers=hr).json()
        assert moved["submitted_job_id"] == source and moved["job_id"] == target
        assert moved["active_version_id"] == detail["active_version_id"]
        current_jobs = jobs(hr)
        assert current_jobs[source]["application_count"] == 0 and current_jobs[target]["application_count"] == 1
        files = {"file":("synthetic.docx",data,"application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        assert client.post(f"/api/v1/applications?job_id={target}&candidate_application_id={app_id}",
                           headers=hr,files=files).status_code == 409
        original_slot = client.post(f"/api/v1/applications?job_id={source}&candidate_application_id={app_id}",
                                    headers=hr,files=files)
        assert original_slot.status_code == 202, original_slot.text
        duplicate_move = client.post(base+"/transfer",headers=hr,json={
            "job_id":source,"workflow_version":moved["workflow_version"],"reason":"已有相同候选人",
        })
        assert duplicate_move.status_code == 409
        new_app = original_slot.json()["application_id"]
        assert client.post(f"/api/v1/hr/applications/{new_app}/purge",headers=hr,
                           json={"reason":"清理合成用例"}).status_code == 200
        with owner_engine.connect() as cn:
            event = cn.execute(text("""
                SELECT detail FROM audit_log WHERE target_id=:a AND action='application_transfer' ORDER BY id DESC LIMIT 1
            """), {"a":str(app_id)}).scalar_one()
            assert (event["from_job_id"],event["to_job_id"]) == (source,target)
        assert client.post(base+"/purge",headers=hr,json={"reason":"清理合成用例"}).status_code == 200
