from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import uploads
from app.core.db import owner_engine
from app.main import app
from test_v1_workflow import docx_bytes, login
from worker.tasks.parse import process_resume


def test_atlas_scoped_filters_and_gender(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    atlas = "/api/v1/statistics/atlas"
    with TestClient(app) as client:
        hr = login(client, "hr.tech@company.internal", "hr123456")
        admin = login(client, "admin@company.internal", "admin123")
        other = login(client, "hr.market@company.internal", "dev123456")
        field = {"field_key":"profile.gender","purpose":"preview","reason":"测试简历显式披露"}
        with owner_engine.connect() as db:
            original = db.execute(text("""
                SELECT p.allowed FROM field_usage_policy p JOIN search_state USING(policy_version)
                WHERE p.field_key='profile.gender' AND p.purpose='preview'
            """)).scalar_one_or_none()
        assert client.post("/api/v1/admin/field-policies",headers=admin,json={**field,"allowed":True}).status_code == 200
        job = client.post("/api/v1/hr/jobs",headers=hr,json={"title":"地图测试","department_id":1}).json()["id"]
        apps = []
        try:
            for name,gender,school,degree,major in (
                ("地图测试甲","城市：南京  性别：女","南京大学","硕士","计算机科学"),
                ("地图测试乙","城市：南京","南京信息工程大学","本科","气象学"),
                ("地图测试丙","Gender: Male","浙江大学","博士","药学"),
            ):
                doc = docx_bytes(["基本信息","姓名："+name,gender,"教育经历","学校："+school,"学历："+degree,"专业："+major])
                res = client.post(f"/api/v1/applications?job_id={job}",headers=hr,files={
                    "file":("atlas.docx",doc,"application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                })
                assert res.status_code == 202,res.text
                aid = res.json()["application_id"]
                apps.append(aid)
                path = f"/api/v1/hr/applications/{aid}"
                current = client.get(path+"?include_evidence=false",headers=hr).json()
                res = client.put(path+"/stage",headers=hr,json={"code":"hired","workflow_version":current["workflow_version"]})
                assert res.status_code == 200,res.text
            def report(headers,**params):
                response = client.get(atlas,headers=headers,params={"job_id":job,**params})
                assert response.status_code == 200,response.text
                return response.json()
            data = report(hr)
            assert (data["applications"],data["hired"],data["gender"]) == (3,3,{"female":1,"male":1,"unknown":1})
            assert {r["id"]:r["hired"] for r in data["regions"]} == {"320000":2,"330000":1}
            assert client.get(atlas,headers=other,params={"job_id":job}).status_code == 404
            assert report(admin)["scope"] == "super_admin"
            assert "candidate_id" not in str(data) and "地图测试甲" not in str(data)
            selected = report(hr,region="320000")
            assert selected["hired"] == 2 and sum(r["hired"] for r in selected["trend"]) == 2
            schools = {s["school"]:s for s in selected["schools"]}
            assert schools["南京大学"]["tier"] == "is_985"
            assert schools["南京信息工程大学"]["tier"] == "is_double_first_class"
            assert report(hr,region="320000",tier="is_211",degree="master")["hired"] == 1
            assert report(hr,region="320000",tier="qs",degree="bachelor")["hired"] == 0
            assert report(hr,region="320000",school_id=schools["南京大学"]["id"],major="计算机科学")["hired"] == 1
            assert report(hr,region="330000",school_id=schools["南京大学"]["id"])["hired"] == 0
            assert client.post("/api/v1/admin/field-policies",headers=admin,json={**field,"allowed":False}).status_code == 200
            assert report(hr)["gender"] == {} and not report(hr)["gender_allowed"]
        finally:
            for aid in apps:
                client.post(f"/api/v1/hr/applications/{aid}/purge",headers=hr,json={"reason":"清理统计测试数据"})
            with owner_engine.begin() as db:
                db.execute(text("DELETE FROM job WHERE id=:id"),{"id":job})
            client.post("/api/v1/admin/field-policies",headers=admin,json={**field,"allowed":bool(original)})
