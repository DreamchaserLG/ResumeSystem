"""List jobs, their interview plans, and the count of linked applications."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth
from app.core.db import rls_session

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(auth: AuthContext = Depends(require_auth)):
    admin = auth.role == "super_admin"
    if admin:
        counts = "SELECT job_id,application_count FROM admin_job_application_counts()"
        scope = ""
    else:
        counts = """
            SELECT job_id,count(*) AS application_count FROM application
            WHERE deleted_at IS NULL AND handling_department_id=ANY(CAST(:deps AS bigint[])) GROUP BY job_id
        """
        scope = """WHERE j.department_id=ANY(CAST(:deps AS bigint[])) OR c.application_count>0"""
    with rls_session(departments=None if admin else auth.departments, user_id=auth.user_id) as s:
        rows = s.execute(text(f"""
            WITH counts AS ({counts})
            SELECT j.id,j.title,j.jd,j.status,j.department_id,d.name AS department_name,j.planned_headcount,j.target_date,
                   coalesce(c.application_count,0) AS application_count,
                   j.department_id=ANY(CAST(:deps AS bigint[])) AS manageable,
                   coalesce((SELECT jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'position',r.position)
                                              ORDER BY r.position)
                             FROM job_interview_round r WHERE r.job_id=j.id),'[]'::jsonb) AS rounds
            FROM job j JOIN department d ON d.id=j.department_id
            LEFT JOIN counts c ON c.job_id=j.id
            {scope} ORDER BY d.name,j.id
        """), {"deps": auth.departments}).mappings().all()
    return {"items": [dict(row) for row in rows], "scope": auth.role}
