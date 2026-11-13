"""Exercise the upload → parsing → review → screening → decision workflow."""
import io

from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import uploads
from app.core.db import owner_engine
from app.main import app
from worker.tasks.parse import process_resume


def docx_bytes(lines):
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def login(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"X-Session-Token": response.json()["token"]}


def test_workflow_with_real_docx(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    data = docx_bytes([
        "基本信息", "姓名：测试候选人", "城市：杭州", "婚育情况：受限内容",
        "教育经历", "学校：清华大学", "学历：本科", "专业：计算机",
        "工作经历", "公司：示例科技有限公司", "岗位：后端工程师", "时间：2020.01–2024.12",
        "负责 Go 微服务和订单中台，峰值 QPS 达到 22k", "社区维护：协助开源项目",
        "项目经历", "项目名称：校园应用", "使用 Java 完成校园系统",
        "教育经历", "学校：浙江大学", "学历：看不清",
    ])
    with TestClient(app) as client:
        hr = login(client, "hr.tech@company.internal", "hr123456")
        other = login(client, "hr.market@company.internal", "dev123456")
        admin = login(client, "admin@company.internal", "admin123")
        def upload(job):
            return client.post(f"/api/v1/applications?job_id={job}", headers=hr,
                               files={"file": ("synthetic.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert upload(3).status_code == 404
        response = upload(1)
        assert response.status_code == 202, response.text
        application_id = response.json()["application_id"]
        base = f"/api/v1/hr/applications/{application_id}"
        initial = client.get(base, headers=hr)
        assert initial.status_code == 200, initial.text
        info = initial.json()
        version_id = info["active_version_id"]
        assert info["extractor_version"] == "rules-v3"
        assert "受限内容" not in initial.text
        assert client.get(base, headers=other).status_code == 404
        assert client.get(base, headers=admin).status_code == 403
        with owner_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM resume_search_record WHERE version_id=:v"), {"v": version_id}).scalar_one() > 0
        query = {"job_id":1, "section":"work", "skill":"Go", "min_months":36, "keywords":["高并发","订单"]}
        result = client.post("/api/v1/hr/search", headers=hr, json=query)
        assert result.status_code == 200, result.text
        rows = result.json()["items"]
        assert [row["id"] for row in rows] == [application_id]
        assert any("QPS" in e["text"] for e in rows[0]["evidence"])
        assert client.post("/api/v1/hr/search", headers=hr, json=query).json()["cached"] is True
        wrong = client.post("/api/v1/hr/search", headers=hr, json={**query, "skill":"Java", "status":"review"})
        assert wrong.status_code == 200, wrong.text
        assert application_id in [row["id"] for row in wrong.json()["items"]]
        queue = client.get("/api/v1/hr/review", headers=hr).json()
        assert application_id in [row["id"] for row in queue["items"]]
        issue = next(i for i in info["issues"] if i["field_key"] == "education.degree_level")
        correction = {"version_id":version_id,"record_no":issue["record_no"],"field_key":issue["field_key"],
                      "action":"correct","value":"硕士","reason":"核对原件"}
        fixed = client.post(base + "/corrections", headers=hr, json=correction)
        assert fixed.status_code == 200, fixed.text
        new_version = fixed.json()["version_id"]
        assert version_id != new_version
        assert client.post(base + "/corrections", headers=hr, json=correction).status_code == 409
        ext = next(item for item in info["issues"] if item["field_key"] == "extension")
        extended = client.post(base+"/corrections",headers=hr,json={
            "version_id":new_version,"record_no":ext["record_no"],"field_key":"extension",
            "action":"correct","value":"维护开源工具","reason":"已核实扩展字段",
        })
        assert extended.status_code == 200, extended.text
        monkeypatch.setattr("app.api.hr.schedule_parse", lambda version: process_resume.run(version))
        retry = client.post(base + "/reparse", headers=hr)
        assert retry.status_code == 202, retry.text
        reparsed = retry.json()["version_id"]
        current = client.get(base,headers=hr).json()
        assert current["active_version_id"] == reparsed
        assert any(e["field_key"]=="education.degree_level" and e["text"]=="硕士"
                   and e["extraction_method"]=="manual" for e in current["evidence"])
        assert any(field["value"]=="维护开源工具" for field in current["reviewed_extensions"])
        with owner_engine.connect() as conn:
            assert conn.execute(text("""
                SELECT degree_level FROM resume_education WHERE version_id=:v AND record_no=:r
            """), {"v":reparsed,"r":issue["record_no"]}).scalar_one() == "master"
            assert conn.execute(text("""
                SELECT version_id FROM application_field_review
                WHERE application_id=:a AND field_key='education.degree_level'
            """), {"a":application_id}).scalar_one() == new_version
        new_version = reparsed
        first_round = info["rounds"][0]["id"]
        stage = client.put(base + "/stage", headers=hr, json={
            "code":"interview","round_id":first_round,"workflow_version":info["workflow_version"],
        })
        assert stage.status_code == 200, stage.text
        assert stage.json()["stage"]["label"] == "一面"
        assert client.get(base, headers=hr).json()["status"] == "active"
        assert client.post(base + "/transfer", headers=hr, json={
            "job_id":3,"workflow_version":stage.json()["workflow_version"],"reason":"交由其他部门跟进",
        }).status_code == 200
        assert client.get(base, headers=hr).status_code == 404
        moved = client.get(base, headers=other)
        assert moved.status_code == 200
        assert moved.json()["job_id"] == 3 and moved.json()["submitted_job_id"] == 1
        assert moved.json()["stage"]["label"] == "待审核"
        assert moved.json()["active_version_id"] == new_version
        deleted = client.post(base + "/purge", headers=other, json={"reason":"验收样本清理"})
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["cleanup_pending"] == 0
        assert client.get(base, headers=other).status_code == 404
