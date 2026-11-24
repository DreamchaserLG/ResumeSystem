"""List jobs, their interview plans, and the count of linked applications."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth, require_recruitment_access
from app.core.cards import CardQuery, cards
from app.core.db import rls_session
from app.core.screening import VISIBLE
from app.core.vector_retrieval import JOB_CANDIDATE_SCOPE, retrieval
from app.core.workflow import audit

router = APIRouter(prefix="/jobs", tags=["jobs"])
REFERENCE_COLUMNS = """
    coalesce(ref.category,j.source_category) AS category,coalesce(ref.sources,'[]'::jsonb) AS jd_sources,
    coalesce(ref.jd_snapshot=j.jd AND ref.requirements_snapshot=j.requirements,false) AS reference_current,
    ref.researched_on AS jd_researched_on,
    coalesce(ref.reference_recruitment_start=j.recruitment_start
        AND ref.reference_recruitment_end=j.recruitment_end,false) AS recruitment_window_is_reference
"""


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
        scope = """AND (j.department_id=ANY(CAST(:deps AS bigint[])) OR c.application_count>0)"""
    with rls_session(departments=None if admin else auth.departments, user_id=auth.user_id) as s:
        rows = s.execute(text(f"""
            WITH counts AS ({counts})
            SELECT j.id,j.title,j.jd,j.status,j.department_id,d.name AS department_name,j.planned_headcount,j.target_date,j.recruitment_type,j.recruiter_name,j.updated_at,
                   j.requirements,j.required_skills,j.experience_tags,j.recruitment_start,j.recruitment_end,
                   j.source_category,j.work_location,j.employment_type,
                   {REFERENCE_COLUMNS},
                   d.company_id,company.name AS company_name,
                   coalesce(recruiter.id,CASE WHEN manager.total=1 THEN manager.only_id END) AS recruiter_user_id,
                   coalesce(recruiter.display_name,CASE WHEN manager.total=1 THEN manager.only_name END) AS department_recruiter_name,
                   coalesce(c.application_count,0) AS application_count,
                   (CAST(:admin AS boolean) OR j.department_id=ANY(CAST(:deps AS bigint[]))) AS manageable,
                   coalesce((SELECT jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'position',r.position)
                                              ORDER BY r.position)
                             FROM job_interview_round r WHERE r.job_id=j.id),'[]'::jsonb) AS rounds
            FROM job j JOIN department d ON d.id=j.department_id
            JOIN recruiting_company company ON company.id=d.company_id
            LEFT JOIN counts c ON c.job_id=j.id
            LEFT JOIN job_posting_reference ref ON ref.job_id=j.id
            LEFT JOIN app_user recruiter ON recruiter.id=j.recruiter_user_id AND recruiter.status='active'
              AND EXISTS (SELECT 1 FROM department_admin da WHERE da.user_id=recruiter.id)
            LEFT JOIN LATERAL (
                SELECT count(*) AS total,min(u.id) AS only_id,min(u.display_name) AS only_name
                FROM department_admin da JOIN app_user u ON u.id=da.user_id AND u.status='active'
                WHERE da.department_id=j.department_id
            ) manager ON true
            WHERE NOT j.hidden {scope} ORDER BY d.name,j.id
        """), {"deps": auth.departments,"admin":admin}).mappings().all()
        departments = s.execute(text("""
            SELECT d.id,d.name,d.company_id,c.name AS company_name,
                   coalesce((SELECT jsonb_agg(jsonb_build_object('id',u.id,'display_name',u.display_name)
                                    ORDER BY u.display_name,u.id)
                             FROM department_admin da JOIN app_user u ON u.id=da.user_id AND u.status='active'
                             WHERE da.department_id=d.id),'[]'::jsonb) AS managers
            FROM department d JOIN recruiting_company c ON c.id=d.company_id
            WHERE d.status='active' AND (:admin OR d.id=ANY(CAST(:deps AS bigint[])))
            ORDER BY c.id,d.name
        """), {"deps":auth.departments,"admin":admin}).mappings().all()
        companies = s.execute(text("SELECT id,name,parent_id FROM recruiting_company ORDER BY id")).mappings().all() if admin else []
        recruiters = [dict(row) for row in s.execute(text("""
            SELECT u.id,u.display_name,array_agg(d.name ORDER BY d.name) AS department_names
            FROM app_user u JOIN department_admin da ON da.user_id=u.id
            JOIN department d ON d.id=da.department_id
            WHERE u.status='active' GROUP BY u.id ORDER BY u.display_name,u.id
        """)).mappings()] if admin else []
    return {"items": [dict(row) for row in rows], "scope": auth.role,
            "departments":[dict(row) for row in departments], "companies":companies,
            "recruiters":recruiters}


def _visible_job(session, auth, job_id):
    job = session.execute(text(f"""
        SELECT j.id,j.title,j.status,j.department_id,j.jd,j.requirements,j.required_skills,j.experience_tags,
               j.recruitment_start,j.recruitment_end,j.target_date,d.name AS department_name,
               j.source_category,j.work_location,j.employment_type,
               {REFERENCE_COLUMNS}
        FROM job j JOIN department d ON d.id=j.department_id
        LEFT JOIN job_posting_reference ref ON ref.job_id=j.id
        WHERE j.id=:id AND NOT j.hidden AND (j.department_id=ANY(CAST(:deps AS bigint[])) OR EXISTS (
            SELECT 1 FROM application a WHERE a.job_id=j.id AND {VISIBLE}))
    """), {"id":job_id,"deps":auth.departments}).mappings().one_or_none()
    if job is None:
        raise HTTPException(404,"岗位不存在或超出当前权限")
    return dict(job)


@router.get("/{job_id}/candidates")
def job_candidates(job_id: int, page: int = Query(1, ge=1, le=10000), size: int = Query(20, ge=1, le=100),
                   ranking: Literal["sql", "hybrid"] = "hybrid",
                   pool: Literal["linked", "unassigned"] = "linked",
                   auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        job = _visible_job(session, auth, job_id)
        unassigned = pool == "unassigned"
        match = retrieval(session, auth.departments, job_id, unassigned=unassigned) if ranking == "hybrid" else None
        indexed = match is not None and match["source"] != "pending"
        request = CardQuery(job_id=None if unassigned else job_id,
                            department_id=job["department_id"] if unassigned else None,
                            sort="recent" if indexed else "match", page=page, size=size)
        page_data = cards(session, auth, request, show_match=True,
                          ranked_ids=match["order"] if indexed else None, unassigned_only=unassigned)
        page_data["unassigned_total"] = session.execute(text(f"""
            SELECT count(*) FROM application a WHERE {VISIBLE} AND a.job_id IS NULL
              AND a.status<>'closed' AND a.handling_department_id=:department
        """), {"deps": auth.departments, "department": job["department_id"]}).scalar_one()
        if match:
            page_data["retrieval"] = {key: match[key] for key in ("source", "eligible", "indexed")}
            for item in page_data["items"]:
                hit = match["candidates"].get(item["id"])
                if hit and hit["version_id"] == item["version_id"]:
                    item["retrieval_status"] = hit["status"]
                    item["skill_coverage"] = hit["skill_coverage"]
                    item["match_score"] = hit["score"]
                    if hit["score"] is not None:
                        item["match_percent"] = hit["score"]["total"]
    return {"job": job, **page_data}


@router.get("/{job_id}/candidates/{application_id}/evidence")
def job_candidate_evidence(job_id: int, application_id: int,
                           pool: Literal["linked", "unassigned"] = "linked",
                           auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        _visible_job(session, auth, job_id)
        unassigned = pool == "unassigned"
        application = session.execute(text(f"""
            SELECT a.active_version_id FROM application a
            WHERE {JOB_CANDIDATE_SCOPE} AND a.id=:application AND {VISIBLE}
        """), {"job": job_id, "application": application_id, "deps": auth.departments,
                "unassigned": unassigned}).one_or_none()
        if application is None:
            raise HTTPException(404, "候选人不存在或超出当前权限")
        match = retrieval(session, auth.departments, job_id, application_id, unassigned=unassigned)
        candidate = match["candidates"].get(application_id)
        if candidate and candidate["version_id"] != application.active_version_id:
            raise HTTPException(409, "简历版本已更新，请刷新")
        audit(session, auth.user_id, "view_job_candidate_evidence", application_id,
              {"job_id": job_id, "version_id": application.active_version_id})
        return {"source": match["source"], "version_id": application.active_version_id,
                "status": candidate["status"] if candidate else None,
                "skill_coverage": candidate["skill_coverage"] if candidate else None,
                "score": candidate["score"] if candidate else None,
                "requirements": match.get("details", []), "evidence_blocks": match.get("evidence_blocks", [])}
