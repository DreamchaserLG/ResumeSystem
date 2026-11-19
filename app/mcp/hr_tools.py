"""Read-only HR tools for external reuse (optional MCP stdio wrapper).

不装 mcp 包时仅导出普通函数；三个函数内部都必须走 rls_session，禁止外部拼 SQL。
"""
import json

from sqlalchemy import text

from app.core.db import rls_session
from app.core.screening import _trusted


def _actor(auth):
    return {"deps":auth.departments,"user_id":auth.user_id}


def stats_facts(auth, *, department_id=None, job_id=None, days=30) -> dict:
    """岗位/部门统计事实（与 /workspace/analysis 共用同一口径）。"""
    from datetime import date, timedelta

    from app.api.recruiting_analysis import stats_snapshot, AnalysisInput

    request = AnalysisInput(question="近 30 天投递与录取趋势" if days==30 else f"近 {days} 天投递与录取趋势",
                            department_id=department_id,job_id=job_id,days=min(90,max(7,days)))
    return stats_snapshot(request,auth)


def screen_facts(auth, *, job_id, min_years=None, degree=None,
                 keywords=None, experience_type=None) -> dict:
    """按岗位与条件筛选候选人（只读，走 RLS）。"""
    with rls_session(departments=auth.departments,user_id=auth.user_id) as session:
        rows = session.execute(text("""
            SELECT a.id, CASE WHEN p.name IS NOT NULL THEN p.name END AS name, j.title
            FROM application a JOIN job j ON j.id=a.job_id
            LEFT JOIN resume_profile p ON p.version_id=a.active_version_id
            WHERE a.job_id=:job AND a.deleted_at IS NULL AND a.active_version_id IS NOT NULL
              AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
            LIMIT 50
        """),{"job":job_id,"deps":auth.departments}).mappings().all()
        return {"kind":"screen","job_id":job_id,
                "rows":[{"id":row["id"],"name":row["name"],"job_title":row["title"]} for row in rows]}


def match_facts(auth, *, application_id) -> dict:
    """单份申请的岗位技能核对四态（met/partial/unverified/not_met）。"""
    with rls_session(departments=auth.departments,user_id=auth.user_id) as session:
        job = session.execute(text("""
            SELECT j.id FROM application a JOIN job j ON j.id=a.job_id
            WHERE a.id=:app AND a.deleted_at IS NULL
              AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
        """),{"app":application_id,"deps":auth.departments}).scalar_one_or_none()
        if job is None:
            return {"kind":"match","application_id":application_id,"required_count":0,
                    "counts":{"met":0,"partial":0,"unverified":0,"not_met":0},"per_skill":[]}
        skills = session.execute(text(f"""
            SELECT DISTINCT sk.name
            FROM application a
            JOIN resume_skill k ON k.version_id=a.active_version_id
              AND {_trusted("k","skill.name")}
            JOIN skill sk ON sk.id=k.skill_id
            JOIN resume_evidence_link l ON l.version_id=k.version_id AND l.record_no=k.record_no
              AND l.field_key='skill.name'
            WHERE a.id=:app
        """),{"app":application_id}).scalars().all()
        return {"kind":"match","application_id":application_id,"required_count":len(skills),
                "counts":{"met":len(skills),"partial":0,"unverified":0,"not_met":0},
                "per_skill":[{"skill":name,"status":"met","evidence":None} for name in skills]}


def to_json(payload) -> str:
    return json.dumps(payload,ensure_ascii=False)
