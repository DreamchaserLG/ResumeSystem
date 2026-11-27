"""验证看板按时间、状态、部门和岗位交叉筛选。"""
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import statistics
from app.core.auth import AuthContext, require_recruitment_access
from app.core.db import owner_engine
from app.main import app


def test_board_selection_period_status_and_scope(monkeypatch):
    actor = [AuthContext(1, "", "", "super_admin", 1, [1, 2])]
    monkeypatch.setitem(app.dependency_overrides, require_recruitment_access, lambda: actor[0])
    with owner_engine.begin() as db:
        @contextmanager
        def read(**options):
            with db.begin_nested():
                db.execute(text("SET LOCAL ROLE rs_app"))
                db.execute(text("SELECT set_config('app.user_id','1',true),set_config('app.departments','1,2',true)"))
                yield db
                db.execute(text("RESET ROLE"))

        monkeypatch.setattr(statistics, "rls_session", read)

        def create_job(name, department, status):
            return db.execute(text("INSERT INTO job(title,department_id,status) VALUES (:title,:dept,:status) RETURNING id"),
                              {"title": name, "dept": department, "status": status}).scalar_one()

        main = create_job("看板筛选岗位", 1, "open")
        other = create_job("另一部门岗位", 2, "closed")

        def applicant(dept, job, days, tracked=False):
            candidate = db.execute(text("INSERT INTO candidate DEFAULT VALUES RETURNING id")).scalar_one()
            sha = uuid4().hex
            document = db.execute(text("""
                INSERT INTO resume_document(candidate_id,object_key,mime_type,sha256)
                VALUES(:person,:key,'application/pdf',:key) RETURNING id
            """), {"person": candidate, "key": sha}).scalar_one()
            version = db.execute(text("""
                INSERT INTO resume_version(document_id,version_no,parser_version)
                VALUES (:doc,1,'test') RETURNING id
            """), {"doc": document}).scalar_one()
            for day in days:
                application = db.execute(text("""
                    INSERT INTO application(job_id,candidate_id,submitted_department_id,handling_department_id,
                                            submitted_version_id,submitted_at)
                    VALUES (:job,:person,:dept,:dept,:version,CAST(:day AS timestamptz)) RETURNING id
                """), {"job": job, "person": candidate, "dept": dept, "version": version,
                        "day": day+"T09:00:00+08:00"}).scalar_one()
            if tracked:
                db.execute(text("""
                    INSERT INTO talent(candidate_id,active_version_id,source_application_id,status)
                    VALUES (:person,:version,:application,'interviewing')
                """), {"person": candidate, "version": version, "application": application})

        applicant(1, main, ["2024-03-12", "2024-03-13"], tracked=True)
        applicant(1, main, ["2024-03-01"])
        applicant(1, main, ["2024-01-20"])
        applicant(2, other, ["2024-03-12"])

        with TestClient(app) as client:
            path = "/api/v1/statistics/board"

            def select(period, **filters):
                response = client.get(path, params={"period": period, "anchor": "2024-03-12", **filters})
                assert response.status_code == 200, response.text
                return response.json()

            week = select("week", department_id=1, job_id=main)
            assert week["period"]["start"] == "2024-03-11"
            assert week["selection"]["people"] == 1
            assert week["selection"]["statuses"] == {"interviewing": 1}
            assert week["totals"]["applications"] == 2
            month = select("month", job_id=main, status="unassigned")
            assert month["scope"]["status"] == "unassigned" and month["selection"]["people"] == 1
            assert month["selection"]["jobs"][0]["id"] == main
            assert select("year", job_id=main)["selection"]["people"] == 3
            assert select("week", job_id=other, job_status="open")["selection"]["people"] == 0
            assert select("week", job_id=other, job_status="closed")["selection"]["people"] == 1
            assert "demographics" not in week
            db.execute(text("UPDATE job SET hidden=true WHERE id=:job"), {"job": main})
            hidden = select("week", department_id=1, job_id=main)
            assert "job_unavailable" in hidden["scope"]["adjustments"]
            assert main not in {item["id"] for item in hidden["selection"]["jobs"]}
            actor[0] = AuthContext(2, "", "", "dept_hr", 1, [1])
            assert client.get(path).status_code == 403
        db.rollback()


def test_board_reads_the_live_database_session(monkeypatch):
    admin = AuthContext(1, "", "", "super_admin", 1, [1, 2])
    monkeypatch.setitem(app.dependency_overrides, require_recruitment_access, lambda: admin)
    with TestClient(app) as client:
        response = client.get("/api/v1/statistics/board", params={"period": "month"})
    assert response.status_code == 200, response.text
    assert response.json()["period"]["kind"] == "month"
    assert "selection" in response.json()
