"""Verify the HR cards API with real multipage PDF and PostgreSQL authorization."""
import io

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf
from sqlalchemy import text

from app.api import uploads
from app.core.db import owner_engine
from app.main import app
from worker.tasks.parse import process_resume


def pdf_resume():
    doc = pymupdf.open()
    first = doc.new_page()
    for row, line in enumerate((
        "Profile", "Name: Resume QA", "Target role: Legal Engineer", "Gender: female",
        "Phone: 13800000001", "Address: Example Street 1", "Education",
        "School: Tsinghua", "Degree: Master",
        "Work Experience", "Company: Example Law Firm Ltd.",
        "Dates: 2021.01-2025.12", "Go and legal work at a law firm",
    )):
        first.insert_text((60, 55 + row * 24), line, fontsize=13)
    # Synthetic photo region has no real personal data.
    pic = Image.new("RGB", (120, 160), (200, 180, 170))
    pic.paste(Image.new("RGB", (60, 60), (120, 100, 90)), (30, 20))
    buffer = io.BytesIO()
    pic.save(buffer, format="PNG")
    first.insert_image(pymupdf.Rect(420, 50, 540, 210), stream=buffer.getvalue())
    second = doc.new_page()
    second.insert_text((60, 70), "Second page preserves the document layout", fontsize=15)
    data = doc.tobytes()
    doc.close()
    return data


def login(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"X-Session-Token": response.json()["token"]}


def test_resume_cards_scope_filter_pagination_and_preview(monkeypatch):
    monkeypatch.setattr(uploads, "schedule_parse", lambda version: process_resume.run(version))
    with TestClient(app) as client:
        hr = login(client, "hr.tech@company.internal", "hr123456")
        other = login(client, "hr.market@company.internal", "dev123456")
        admin = login(client, "admin@company.internal", "admin123")
        policy_path = "/api/v1/admin/field-policies"
        keys = ("contact.phone","profile.gender","profile.address","profile.avatar")
        original = {}
        with owner_engine.connect() as db:
            original = dict(db.execute(text("""
                SELECT field_key,allowed FROM field_usage_policy JOIN search_state USING(policy_version)
                WHERE purpose='preview'
            """)).all())
        try:
            for field in keys:
                policy = client.post(policy_path, headers=admin, json={
                    "field_key":field,"purpose":"preview","allowed":True,"reason":"验收测试"})
                assert policy.status_code == 200, policy.text
            assert client.post(policy_path, headers=admin, json={
                "field_key":"profile.gender","purpose":"filter","allowed":True,"reason":"验收测试"}).status_code == 422
            response = client.post("/api/v1/applications?job_id=1", headers=hr,
                                   files={"file":("two-pages.pdf",pdf_resume(),"application/pdf")})
            assert response.status_code == 202, response.text
            app_id = response.json()["application_id"]
            version = response.json()["version_id"]
            path = "/api/v1/hr/resume-cards/search"
            result = client.post(path, headers=hr, json={"query":"Resume QA","size":1})
            assert result.status_code == 200, result.text
            assert result.json()["total"] == 1
            row = result.json()["items"][0]
            assert row["id"] == app_id
            assert row["target_role"] == "Legal Engineer"
            assert row["gender"] == "female" and row["phone"] == "13800000001" and row["address"] == "Example Street 1"
            assert row["has_avatar"] is True and row["page_count"] == 2
            assert {"degree:master","domain:legal","skill:go","experience:5plus"} <= {tag["id"] for tag in row["tags"]}
            assert client.post(path, headers=hr, json={
                "query":"Legal","tags":["degree:master","domain:legal","skill:go"],"size":1
            }).json()["total"] == 1
            assert client.post(path, headers=hr, json={"query":"13800000001"}).json()["total"] == 0
            assert client.post(path, headers=hr, json={"query":"Example Street 1"}).json()["total"] == 0
            assert client.post(path, headers=other, json={"query":"Resume QA"}).json()["total"] == 0
            assert "school:is_985" in {tag["id"] for tag in row["tags"]}
            assert client.post(path, headers=hr, json={"tags":["school:is_985"],"job_id":1}).status_code == 403
            school_path = "/api/v1/admin/school-filters"
            approval = {"job_id":1,"tag_code":"is_985","enabled":True,"reason":"验收测试"}
            assert client.post(school_path, headers=admin, json=approval).status_code == 200
            assert client.post(path, headers=hr, json={"tags":["school:is_985","degree:master"],"job_id":1}).json()["total"] == 1
            assert client.post(school_path, headers=admin, json={**approval,"enabled":False}).status_code == 200
            beyond = client.post(path, headers=hr, json={"query":"Resume QA","page":2,"size":1}).json()
            assert beyond["total"] == 1 and beyond["items"] == []
            base = f"/api/v1/hr/applications/{app_id}"
            image1 = client.get(base+f"/preview?page=1&version={version}", headers=hr)
            image2 = client.get(base+f"/preview?page=2&version={version}", headers=hr)
            assert image1.status_code == image2.status_code == 200
            assert image1.content != image2.content
            assert client.get(base+"/preview?page=3", headers=hr).status_code == 404
            assert client.get(base+"/preview?page=2", headers=other).status_code == 404
            assert client.get(base+"/avatar", headers=hr).status_code == 200
            assert client.get(base+"/avatar", headers=other).status_code == 404
            assert client.post(base+"/purge", headers=hr, json={"reason":"清理合成测试"}).json()["cleanup_pending"] == 0
        finally:
            for field in keys:
                assert client.post(policy_path, headers=admin, json={
                    "field_key":field,"purpose":"preview","allowed":original.get(field,False),"reason":"恢复测试前权限"}).status_code == 200
