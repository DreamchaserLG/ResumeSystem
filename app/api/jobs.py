"""List jobs, their interview plans, and the count of linked applications."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth, require_recruitment_access
from app.core.cards import CardQuery, cards
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
            SELECT j.id,j.title,j.jd,j.status,j.department_id,d.name AS department_name,j.planned_headcount,j.target_date,j.recruitment_type,j.recruiter_name,j.updated_at,
                   j.requirements,j.required_skills,j.experience_tags,j.recruitment_start,j.recruitment_end,
                   d.company_id,company.name AS company_name,
                   coalesce(c.application_count,0) AS application_count,
                   (CAST(:admin AS boolean) OR j.department_id=ANY(CAST(:deps AS bigint[]))) AS manageable,
                   coalesce((SELECT jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'position',r.position)
                                              ORDER BY r.position)
                             FROM job_interview_round r WHERE r.job_id=j.id),'[]'::jsonb) AS rounds
            FROM job j JOIN department d ON d.id=j.department_id
            JOIN recruiting_company company ON company.id=d.company_id
            LEFT JOIN counts c ON c.job_id=j.id
            {scope} ORDER BY d.name,j.id
        """), {"deps": auth.departments,"admin":admin}).mappings().all()
        departments = s.execute(text("""
            SELECT d.id,d.name,d.company_id,c.name AS company_name
            FROM department d JOIN recruiting_company c ON c.id=d.company_id
            WHERE d.status='active' AND (:admin OR d.id=ANY(CAST(:deps AS bigint[])))
            ORDER BY c.id,d.name
        """), {"deps":auth.departments,"admin":admin}).mappings().all()
        companies = s.execute(text("SELECT id,name,parent_id FROM recruiting_company ORDER BY id")).mappings().all() if admin else []
    return {"items": [dict(row) for row in rows], "scope": auth.role,
            "departments":[dict(row) for row in departments], "companies":[dict(row) for row in companies]}


@router.get("/{job_id}/candidates")
def job_candidates(job_id: int, page: int = Query(1,ge=1,le=10000), size: int = Query(20,ge=1,le=100),
                   auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments,user_id=auth.user_id) as session:
        job = session.execute(text("""
            SELECT j.id,j.title,j.status,j.requirements,j.required_skills,j.experience_tags,
                   j.recruitment_start,j.recruitment_end,j.target_date,d.name AS department_name
            FROM job j JOIN department d ON d.id=j.department_id
            WHERE j.id=:id AND (j.department_id=ANY(CAST(:deps AS bigint[])) OR EXISTS (
                SELECT 1 FROM application a WHERE a.job_id=j.id AND a.deleted_at IS NULL
                  AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))))
        """), {"id":job_id,"deps":auth.departments}).mappings().one_or_none()
        if job is None:
            raise HTTPException(404,"岗位不存在或超出当前权限")
        page_data = cards(session,auth,CardQuery(job_id=job_id,sort="match",page=page,size=size),show_match=True)
    return {"job":dict(job),**page_data}
