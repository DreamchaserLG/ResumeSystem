"""M7 contract and isolation tests with synthetic structured resumes, PostgreSQL and Redis."""
import secrets

from fastapi.testclient import TestClient
import psycopg
import pytest

from app.api import integrations
from app.core import integration
from app.main import app
from migrations.migrate import main as migrate


@pytest.fixture(scope="module")
def seed(dsn):
    assert migrate() == 0
    token = f"rsint_{secrets.token_urlsafe(32)}"
    created = {}
    with psycopg.connect(dsn) as cn:
        def create(query, *args):
            return cn.execute(query, args).fetchone()[0]

        candidate = create("INSERT INTO candidate DEFAULT VALUES RETURNING id")
        created["candidate"] = candidate
        document = create("""INSERT INTO resume_document (candidate_id, object_key, mime_type, sha256)
                              VALUES (%s, 'm7-fixture', 'application/pdf', 'm7-test') RETURNING id""", candidate)
        created["document"] = document
        for index in range(1, 4):
            version = create("""INSERT INTO resume_version (document_id, version_no, parser_version, status, published_at)
                                VALUES (%s, %s, 'fixture-v1', 'published', now()) RETURNING id""", document, index)
            created[f"v{index}"] = version
        first, current, other_version = (created[key] for key in ("v1", "v2", "v3"))
        for key, job, dept, version in [("a", 1, 1, first), ("same_scope", 1, 1, first), ("outside", 3, 3, other_version)]:
            created[key] = create("""INSERT INTO application (job_id, candidate_id, submitted_department_id,
                                     handling_department_id, submitted_version_id, active_version_id, status)
                                     VALUES (%s,%s,%s,%s,%s,%s,'active') RETURNING id""",
                                  job, candidate, dept, dept, version, version)
        cn.execute("UPDATE application SET active_version_id=%s WHERE id=%s", (current, created["a"]))
        cn.execute("UPDATE resume_version SET status='superseded' WHERE id=%s", (first,))
        client = create("""INSERT INTO integration_client(name, allowed_fields, requests_per_second, requests_per_day)
                            VALUES (%s, ARRAY['work.company','work.period','work.description','profile.city','education.degree_level',
                                'x_.test_community','contact.email'], 1000, 1000) RETURNING id""", f"M7 test {token[-8:]}")
        created["client"] = client
        cn.execute("INSERT INTO integration_job (client_id,job_id,handling_department_id) VALUES (%s,1,1)", (client,))
        cn.execute("INSERT INTO integration_api_key(client_id,key_digest) VALUES (%s,%s)", (client, integration.token_digest(token)))
        cn.execute("""INSERT INTO field_definition(field_key, display_name, value_type)
                      VALUES ('x_.test_community','社区贡献','text') ON CONFLICT DO NOTHING""")
        cn.execute("""INSERT INTO field_usage_policy(field_key, purpose, allowed, policy_version)
                      VALUES ('x_.test_community','export',true,1),('contact.email','export',true,1),
                             ('work.description','export',true,1) ON CONFLICT DO NOTHING""")
        cn.execute("""INSERT INTO resume_record(version_id,record_no,kind)
                      VALUES (%s,1,'work'),(%s,2,'profile'),(%s,3,'education'),(%s,4,'extension'),(%s,5,'contact')""",
                   (current,) * 5)
        cn.execute("""INSERT INTO resume_work(version_id,record_no,company,title,start_period_raw,end_period_raw,description)
                      VALUES (%s,1,'示例科技','内部职位','2021','2024.06','优化接口；受限原文')""", (current,))
        cn.execute("INSERT INTO resume_profile(version_id,record_no,name,city) VALUES (%s,2,'示例人员','杭州')", (current,))
        cn.execute("""INSERT INTO resume_education(version_id,record_no,school_raw,degree_level)
                      VALUES (%s,3,'示例大学','bachelor')""", (current,))
        cn.execute("""INSERT INTO resume_contact(version_id,record_no,type,value)
                      VALUES (%s,5,'email','private@example.test')""", (current,))
        spans = ["示例科技 / 内部职位 / private@example.test", "2021–2024.06", "本科", "社区贡献：维护示例项目", "受限原文", None, "优化接口"]
        for number, content in enumerate(spans, 1):
            cn.execute("""INSERT INTO resume_source_span(version_id,span_no,span_kind,raw_text,locator,extraction_method,is_sensitive,reading_order)
                          VALUES (%s,%s,%s,%s,'{"page":1,"bbox":[1,2,3,4],"private_key":"fixture"}','pdf_text',%s,%s)""",
                       (current, number, "photo" if content is None else "text", content, number == 5, number))
        for record, field, span, start, end in [
            (1,"work.company",1,0,4), (1,"work.title",1,7,11), (5,"contact.email",1,14,len(spans[0])),
            (1,"work.period",2,0,len(spans[1])), (1,"work.description",5,0,4), (1,"work.description",7,0,4), (3,"education.degree_level",3,0,2),
            (4,"x_.test_community",4,5,len(spans[3])), (2,"profile.city",5,0,4),
        ]:
            cn.execute("""INSERT INTO resume_evidence_link(version_id,record_no,field_key,span_no,start_offset,end_offset)
                          VALUES (%s,%s,%s,%s,%s,%s)""", (current,record,field,span,start,end))
        cn.execute("""INSERT INTO resume_field_issue(version_id,record_no,field_key,span_no,reason)
                      VALUES (%s,2,'profile.city',5,'low_confidence')""", (current,))
        cn.execute("""INSERT INTO resume_extra_value(version_id,record_no,field_key,raw_label,payload,span_no,rule_version)
                      VALUES (%s,4,'x_.test_community','社区贡献','{"raw":"维护示例项目","internal":"private"}',4,'v1')""", (current,))
    created["headers"] = {"Authorization": f"Bearer {token}"}
    yield created
    with psycopg.connect(dsn) as cn:
        cn.execute("DELETE FROM audit_log WHERE target_type='integration_client' AND target_id=%s", (str(client),))
        cn.execute("DELETE FROM integration_client WHERE id=%s", (client,))
        cn.execute("DELETE FROM application WHERE candidate_id=%s", (candidate,))
        cn.execute("DELETE FROM resume_version WHERE document_id=%s", (document,))
        cn.execute("DELETE FROM resume_document WHERE id=%s", (document,))
        cn.execute("DELETE FROM candidate WHERE id=%s", (candidate,))


@pytest.fixture(scope="module")
def api():
    return TestClient(app)


def test_auth_pagination_and_approved_filters(api, seed):
    assert api.get("/api/v1/applications").status_code == 401
    assert api.get("/api/v1/applications", headers={"Authorization": "Bearer wrong"}).status_code == 401
    headers = seed["headers"]
    response = api.get("/api/v1/applications", headers=headers, params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.headers["X-Policy-Version"] == "1"
    page = response.json()
    assert [row["application_id"] for row in page["items"]] == [seed["a"]]
    following = api.get("/api/v1/applications", headers=headers, params={"limit": 1, "cursor": page["next_cursor"]})
    assert [row["application_id"] for row in following.json()["items"]] == [seed["same_scope"]]
    assert api.get("/api/v1/applications", headers=headers, params={"limit": 2, "cursor": page["next_cursor"]}).status_code == 422
    assert api.get("/api/v1/applications", headers=headers, params={"job_id": 3}).status_code == 404
    assert api.get("/api/v1/applications", headers=headers, params={"school": "示例大学"}).status_code == 403
    assert api.get("/api/v1/applications", headers=headers, params={"city": "杭州"}).json()["items"] == []
    found = api.get("/api/v1/applications", headers=headers, params={"degree_level": "bachelor"})
    assert found.status_code == 200, found.text
    assert [row["application_id"] for row in found.json()["items"]] == [seed["a"]]


def test_detail_contract_and_clipped_evidence(api, seed):
    base = f"/api/v1/applications/{seed['a']}"
    response = api.get(base, headers=seed["headers"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {
        "application_id": seed["a"], "job": {"id": 1, "title": "高级后端工程师（Go）"}, "status": "active",
        "resume_version": {"version_id": seed["v2"], "version_no": 2, "parser_version": "fixture-v1",
                           "published_at": result["resume_version"]["published_at"]},
        "basics": {"_meta": {"record_no": 2, "confidence": None, "extraction": None, "evidence": [],
                            "issues": ["low_confidence"], "fields": {"profile.city": {"confidence": None,
                                "extraction": None, "evidence": [], "issues": ["low_confidence"]}}}},
        "work": result["work"], "education": result["education"], "skills": [], "projects": [],
        "extensions": [{"field_key": "x_.test_community", "label": "社区贡献", "value": "维护示例项目",
                        "type": "text", "confidence": None, "source_span": {"version_id": seed["v2"],
                        "span_no": 4, "start_offset": 5, "end_offset": 11}}], "schema_version": "1.0.0",
    }
    work = result["work"][0]
    assert (work["company"], work["startDate"], work["endDate"]) == ("示例科技", "2021", "2024-06")
    assert "position" not in work and "name" not in result["basics"]
    assert work["summary"] == "优化接口"
    assert result["education"][0]["studyType"] == "bachelor"
    query = {"version_id": seed["v2"]}
    evidence = api.get(f"{base}/evidence/1", headers=seed["headers"], params=query)
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["excerpts"] == [{"field_key": "work.company", "text": "示例科技", "start_offset": 0, "end_offset": 4}]
    assert evidence.json()["locator"] == {"page": 1, "bbox": [1, 2, 3, 4]}
    for span in (5, 6, 999):
        assert api.get(f"{base}/evidence/{span}", headers=seed["headers"], params=query).status_code == 404
    assert api.get(f"{base}/evidence/1", headers=seed["headers"]).status_code == 422


def test_history_scoped_to_application_and_department(api, seed, dsn):
    base = f"/api/v1/applications/{seed['a']}"
    headers = seed["headers"]
    assert api.get(base, headers=headers, params={"version_id": seed["v1"]}).json()["resume_version"]["version_id"] == seed["v1"]
    assert api.get(base, headers=headers, params={"version_id": seed["v3"]}).status_code == 404
    assert api.get(f"/api/v1/applications/{seed['outside']}", headers=headers).status_code == 404
    with psycopg.connect(dsn) as cn:
        cn.execute("UPDATE application SET handling_department_id=3 WHERE id=%s", (seed["a"],))
    try:
        assert api.get(base, headers=headers).status_code == 404
    finally:
        with psycopg.connect(dsn) as cn:
            cn.execute("UPDATE application SET handling_department_id=1 WHERE id=%s", (seed["a"],))


def test_delivery_rechecks_credential_and_audits(api, seed, dsn, monkeypatch):
    base = f"/api/v1/applications/{seed['a']}"
    original = integrations.export_detail

    def read_then_revoke(*args):
        result = original(*args)
        with psycopg.connect(dsn) as cn:
            cn.execute("UPDATE integration_client SET status='disabled' WHERE id=%s", (seed["client"],))
        return result

    monkeypatch.setattr(integrations, "export_detail", read_then_revoke)
    try:
        assert api.get(base, headers=seed["headers"]).status_code == 401
    finally:
        with psycopg.connect(dsn) as cn:
            cn.execute("UPDATE integration_client SET status='active' WHERE id=%s", (seed["client"],))
            events = cn.execute("SELECT action, detail FROM audit_log WHERE target_type='integration_client' AND target_id=%s",
                                (str(seed["client"]),)).fetchall()
    assert {event[0] for event in events} >= {"integration_list", "integration_detail", "integration_evidence"}
    assert all("private@example.test" not in str(detail) and "rsint_" not in str(detail) for _, detail in events)


def test_redis_quota_uses_both_windows_and_reports_rate_limit(api, seed, dsn, monkeypatch):
    from dataclasses import replace
    from time import time

    scope = integration.authenticate(seed["headers"]["Authorization"].split()[1])
    fixed = float((int(time()) // 86400 + 2) * 86400 + 1000)
    limits = replace(scope, requests_per_second=1, requests_per_day=2)
    assert integration.reserve_request(limits, fixed) == (1, 0, False)
    assert integration.reserve_request(limits, fixed) == (0, 1, True)
    assert integration.reserve_request(limits, fixed) == (0, 1, False)
    assert integration.reserve_request(limits, fixed + 1) == (0, 0, False)
    monkeypatch.setattr(integrations, "reserve_request", lambda current: integration.reserve_request(limits, fixed + 2))
    response = api.get("/api/v1/applications", headers=seed["headers"])
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Remaining"] == "0"
    with psycopg.connect(dsn) as cn:
        assert cn.execute("SELECT 1 FROM audit_log WHERE action='integration_rate_limited' AND target_id=%s",
                          (str(seed["client"]),)).fetchone()


def test_field_policy_tightening_after_assembly(api, seed, dsn, monkeypatch):
    base = f"/api/v1/applications/{seed['a']}"
    original = integrations.export_detail

    def read_then_tighten(*args):
        result = original(*args)
        with psycopg.connect(dsn) as cn:
            cn.execute("""UPDATE field_usage_policy SET allowed=false WHERE field_key='work.company'
                          AND purpose='export' AND policy_version=1""")
        return result

    monkeypatch.setattr(integrations, "export_detail", read_then_tighten)
    try:
        assert api.get(base, headers=seed["headers"]).status_code == 409
        monkeypatch.setattr(integrations, "export_detail", original)
        fresh = api.get(base, headers=seed["headers"])
        assert fresh.status_code == 200
        assert "company" not in fresh.json()["work"][0]
        assert api.get(f"{base}/evidence/1", headers=seed["headers"], params={"version_id": seed["v2"]}).status_code == 404
    finally:
        with psycopg.connect(dsn) as cn:
            cn.execute("""UPDATE field_usage_policy SET allowed=true WHERE field_key='work.company'
                          AND purpose='export' AND policy_version=1""")


def test_key_rotation_and_revoke(api, seed, dsn):
    import subprocess
    import sys

    with psycopg.connect(dsn) as cn:
        other_client = cn.execute("INSERT INTO integration_client(name, allowed_fields) VALUES (%s, ARRAY[]::text[]) RETURNING id",
                                  (f"M7 key test {seed['client']}",)).fetchone()[0]
    try:
        def rotate():
            result = subprocess.run([sys.executable, "-m", "app.integration_keys", "rotate", str(other_client)],
                                    capture_output=True, text=True, check=True)
            return {"Authorization": f"Bearer {result.stdout.strip()}"}

        initial = rotate()
        assert api.get("/api/v1/applications", headers=initial).status_code == 200
        new = rotate()
        assert api.get("/api/v1/applications", headers=initial).status_code == 401
        assert api.get("/api/v1/applications", headers=new).status_code == 200
        subprocess.run([sys.executable, "-m", "app.integration_keys", "revoke", str(other_client)], check=True)
        assert api.get("/api/v1/applications", headers=new).status_code == 401
    finally:
        with psycopg.connect(dsn) as cn:
            cn.execute("DELETE FROM audit_log WHERE target_type='integration_client' AND target_id=%s", (str(other_client),))
            cn.execute("DELETE FROM integration_client WHERE id=%s", (other_client,))
