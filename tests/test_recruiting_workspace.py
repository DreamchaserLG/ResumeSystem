"""Exercise upload retries, OR facets and private follow-ups against a migrated database."""
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.api import recruiting_analysis, uploads
from app.core.db import owner_engine
from app.main import app
from test_v1_workflow import docx_bytes, login
from worker.tasks.parse import process_resume


def test_workspace_resume_roundtrip_and_analysis(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    monkeypatch.setattr(recruiting_analysis, "get_settings", lambda: SimpleNamespace(
        recruiting_llm_url="https://dashscope.aliyuncs.com/compatible-mode/v1", recruiting_llm_model="glm-5.3",
        dashscope_api_key=SecretStr("test-key"), recruiting_llm_key=None,
    ))
    provider_inputs = []

    def provider(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        assert request.get_header("Authorization") == "Bearer test-key"
        assert payload["model"] == "glm-5.3"
        data = json.loads(payload["messages"][1]["content"])
        provider_inputs.append(data)
        if payload["stream"]:
            chunk = json.dumps({"choices":[{"delta":{"content":"基于岗位证据核对。"}}]},ensure_ascii=False)
            reasoning = '{"choices":[{"delta":{"reasoning_content":"internal"}}]}'
            return BytesIO((f"data: {reasoning}\n\ndata: {chunk}\n\ndata: [DONE]\n").encode())
        content = ("简历提供了 Python 技能证据，可进一步核实使用场景。" if "verified_skills" in data
                   else json.dumps({"skills":["Python","unknown"]}))
        result = {"choices":[{"message":{"content":content}}]}
        return BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(recruiting_analysis, "urlopen", provider)
    with TestClient(app) as client:
        hr = login(client,"hr.tech@company.internal","hr123456")
        other = login(client,"hr.market@company.internal","dev123456")
        admin = login(client,"admin@company.internal","admin123")
        job_response = client.post("/api/v1/hr/jobs", headers=hr, json={
            "title":"招聘工作台测试", "department_id":1,"jd":"需要 Python 和 PostgreSQL",
            "recruitment_type":"campus","recruiter_name":"招聘同事","rounds":[],
        })
        assert job_response.status_code == 201,job_response.text
        job_id = job_response.json()["id"]
        app_ids = []
        try:
            for name,degree,salary,end in (("测试甲","本科","15-20K","2023.12"),("测试乙","硕士","30K","2025.12")):
                document = docx_bytes(["基本信息","姓名："+name,"期望月薪："+salary,
                    "教育经历","学校：南京大学","学历："+degree,"工作经历","公司：示例科技有限公司",
                    "岗位：后端工程师","时间：2021.01–"+end,"专业技能","Python" if name=="测试甲" else "Java"])
                target = f"/api/v1/applications?job_id={job_id}&import_request={uuid4()}"
                files={"file":("candidate.docx",document,"application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
                uploaded = client.post(target,headers=hr,files=files)
                assert uploaded.status_code==202,uploaded.text
                application_id=uploaded.json()["application_id"]
                app_ids.append(application_id)
                repeated=client.post(target,headers=hr,files=files)
                assert repeated.status_code==202 and repeated.json()["application_id"]==application_id
            path="/api/v1/hr/resume-cards/search"
            def cards(**params):
                response=client.post(path,headers=hr,json={"job_ids":[job_id],**params})
                assert response.status_code==200,response.text
                return response.json()["items"]
            assert len(cards(facets={"degree":["bachelor","master"]}))==2
            salary_rows=cards(facets={"degree":["bachelor","master"],"salary":["10to20"]})
            assert [row["id"] for row in salary_rows]==[app_ids[0]]
            assert salary_rows[0]["salary_midpoint"]==17500
            assert [row["id"] for row in cards(salary_min=17500,salary_max=17500,
                                                experience_min_months=36,experience_max_months=36)]==[app_ids[0]]
            assert [row["id"] for row in cards(salary_min=20000,experience_min_months=37)]==[app_ids[1]]
            assert cards(salary_max=17499)==[]
            assert cards(experience_max_months=35)==[]
            assert client.post(path,headers=hr,json={"salary_min":20000,"salary_max":10000}).status_code==422
            note_path=f"/api/v1/workspace/applications/{app_ids[0]}/followups"
            assert client.post(note_path,headers=other,data={"content":"越权"}).status_code==404
            assert client.get(note_path,headers=admin).status_code==200
            note=client.post(note_path,headers=hr,data={"content":"张总已面过"},files={"attachment":("notes.txt",b"test","text/plain")})
            assert note.status_code==201,note.text
            assert cards(followup="recorded")[0]["latest_note"]=="张总已面过"
            assert cards(followup="unrecorded")[0]["id"]==app_ids[1]
            assert cards(followup_values=["未匹配","张总已面过"])[0]["id"]==app_ids[0]
            options = client.get("/api/v1/workspace/followup-options",headers=hr)
            assert "张总已面过" in options.json()["items"]
            assert "张总已面过" not in client.get("/api/v1/workspace/followup-options",headers=other).json()["items"]
            attachment=note_path+f"/{note.json()['id']}/attachment"
            assert client.get(attachment,headers=other).status_code==404
            assert client.get(attachment,headers=admin).content==b"test"
            admin_cards = client.post(path,headers=admin,json={"job_ids":[job_id]}).json()["items"]
            assert {row["id"] for row in admin_cards}==set(app_ids)
            assert client.get(attachment,headers=hr).content==b"test"
            ranked = cards(sort="match")
            assert [row["id"] for row in ranked] == app_ids
            assert all("match_score" not in row and "evidence_order" not in row for row in ranked)
            job_candidates = client.get(f"/api/v1/jobs/{job_id}/candidates",headers=hr).json()
            assert [row["id"] for row in job_candidates["items"]] == app_ids
            assert [row["match_percent"] for row in job_candidates["items"]] == [50,0]
            assert client.get(f"/api/v1/jobs/{job_id}/candidates",headers=other).status_code == 404

            match_path=f"/api/v1/workspace/applications/{app_ids[0]}/match"
            match=client.post(match_path,headers=hr)
            assert match.status_code==200,match.text
            assert match.json()["skills"]==["Python"]
            assert match.json()["summary_source"]=="llm" and "Python" in match.json()["summary"]
            assert "name" not in match.json()["matches"][0]
            assert "score" not in match.json()["matches"][0]
            assert client.get(match_path,headers=other).json() is None
            assert cards(sort="match")[0]["id"]==app_ids[0]
            report=client.post("/api/v1/workspace/analysis",headers=hr,json={
                "question":"本月投递与录取趋势","job_id":job_id})
            assert report.status_code==200,report.text
            frames=[json.loads(line) for line in report.text.splitlines()]
            assert frames[0]["model"] == "glm-5.3"
            assert "internal" not in report.text
            assert frames[0]["report"]["metrics"]["applications"]==2
            assert frames[-1]=={"event":"done","source":"llm"}
            matches=client.post("/api/v1/workspace/analysis",headers=hr,json={
                "question":"按岗位描述匹配候选人","job_id":job_id})
            report = json.loads(matches.text.splitlines()[0])["report"]
            assert [row["id"] for row in report["matches"]] == app_ids
            assert all("score" not in row for row in report["matches"])
            assert all("测试甲" not in str(data) and "张总已面过" not in str(data) for data in provider_inputs)
            assert client.post("/api/v1/workspace/analysis",headers=other,json={
                "question":"本月投递与录取趋势","job_id":job_id}).status_code==404
        finally:
            for application_id in app_ids:
                client.post(f"/api/v1/hr/applications/{application_id}/purge",headers=hr,json={"reason":"清理测试简历"})
            with owner_engine.begin() as connection:
                connection.execute(text("DELETE FROM job WHERE id=:id"),{"id":job_id})
