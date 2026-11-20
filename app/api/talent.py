"""Talent library and follow-ups for candidates selected from the resume library."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.auth import AuthContext, require_recruitment_access
from app.core.cards import CardQuery, cards
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked
from app.core.screening import _trusted
from app.core.workflow import audit, fresh_departments

router = APIRouter(prefix="/talent", tags=["talent pool"])

Status = Literal["uncontacted", "interviewing", "hired", "rejected", "blacklist", "custom"]
STATUS_OPTIONS = [
    {"code": "uncontacted", "label": "待跟进", "color": "#64748B"},
    {"code": "interviewing", "label": "面试进行中", "color": "#2563B5"},
    {"code": "hired", "label": "录用", "color": "#168357"},
    {"code": "rejected", "label": "淘汰", "color": "#B93848"},
    {"code": "blacklist", "label": "黑名单", "color": "#3C4049"},
    {"code": "custom", "label": "自定义", "color": "#7738A8"},
]
FOLLOWUP_FIELDS = "id,author_id,author_name,body,status,status_label,created_at"

SKILL_TAG = f"""SELECT DISTINCT cg.code,cg.display_order
    FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id
    JOIN capability_group cg ON cg.code=sk.capability_code
    WHERE k.version_id=v.id AND {_trusted("k", "skill.name")}"""

CARD = f"""
    SELECT t.id,t.candidate_id,t.status,t.status_label,t.entered_at,t.updated_at,
           a.id AS application_id,j.title AS job_title,
           d.id AS department_id,d.name AS department_name,
           CASE WHEN flags.name THEN p.name END AS name,
           CASE WHEN flags.city THEN p.city END AS city,
           CASE WHEN flags.role THEN p.target_role END AS target_role,
           v.id AS version_id,v.status AS parse_status,
           CASE WHEN flags.skills THEN ARRAY(
               SELECT c.code FROM ({SKILL_TAG}) c ORDER BY c.display_order,c.code
           ) ELSE ARRAY[]::text[] END AS capabilities
    FROM talent t
    JOIN application a ON a.id=t.source_application_id AND a.deleted_at IS NULL
    JOIN department d ON d.id=a.handling_department_id
    JOIN resume_version v ON v.id=a.active_version_id
    LEFT JOIN resume_profile p ON p.version_id=v.id
    LEFT JOIN job j ON j.id=a.job_id
    CROSS JOIN LATERAL (
        SELECT coalesce(bool_or(field_key='profile.name' AND purpose='preview'),false) AS name,
               coalesce(bool_or(field_key='profile.city' AND purpose='preview'),false) AS city,
               coalesce(bool_or(field_key='profile.target_role' AND purpose='preview'),false) AS role,
               coalesce(bool_or(field_key='skill.name' AND purpose='preview'),false) AS skills,
               coalesce(bool_or(field_key='skill.name' AND purpose='filter'),false) AS filter_skills
        FROM field_usage_policy JOIN field_definition USING(field_key)
        JOIN search_state USING(policy_version)
        WHERE allowed AND field_definition.status='active'
    ) flags
"""


def _lock_talent(connection, talent_id, auth):
    departments = fresh_departments(connection, auth)
    row = connection.execute(text("""
        SELECT t.status,t.status_label FROM application a JOIN talent t ON t.source_application_id=a.id
        WHERE t.id=:id AND a.deleted_at IS NULL AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
        FOR UPDATE OF a,t
    """), {"id":talent_id,"deps":departments}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "人才不存在")
    return row


def _ensure_visible(session, talent_id):
    if not session.execute(text("SELECT 1 FROM talent WHERE id=:id"), {"id":talent_id}).first():
        raise HTTPException(404, "人才不存在")


@router.get("/status-options")
def status_options(auth: AuthContext = Depends(require_recruitment_access)):
    return {"items": STATUS_OPTIONS}


@router.get("")
def list_talent(
    status: Status | None = None,
    department_id: int | None = Query(default=None, gt=0),
    capability: str | None = Query(default=None, max_length=80),
    query: str = Query(default="", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    auth: AuthContext = Depends(require_recruitment_access),
):
    if department_id is not None and auth.role != "super_admin":
        raise HTTPException(403, "部门筛选仅供超级管理员使用")
    where = []
    params = {"status":status,"limit":limit,"offset":offset}
    if department_id is not None:
        where.append("a.handling_department_id=:department")
        params["department"] = department_id
    if query.strip():
        where.append("""(strpos(lower(CASE WHEN flags.name THEN p.name ELSE '' END),lower(:q))>0
            OR strpos(lower(CASE WHEN flags.role THEN p.target_role ELSE '' END),lower(:q))>0)""")
        params["q"] = query.strip()
    if capability:
        where.append(f"flags.filter_skills AND EXISTS (SELECT 1 FROM ({SKILL_TAG}) cap WHERE cap.code=:cap)")
        params["cap"] = capability
    filters = (" WHERE " + " AND ".join(where)) if where else ""
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        rows = session.execute(text(f"""
            WITH filtered AS MATERIALIZED ({CARD}{filters}),
            counts AS (SELECT status,count(*) AS total FROM filtered GROUP BY status),
            paged AS (
                SELECT * FROM filtered WHERE CAST(:status AS text) IS NULL OR status=:status
                ORDER BY updated_at DESC,id DESC LIMIT :limit OFFSET :offset
            )
            SELECT coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.updated_at DESC,p.id DESC) FROM paged p),'[]') AS items,
                   coalesce((SELECT jsonb_object_agg(status,total) FROM counts),'{{}}') AS counts
        """), params).mappings().one()
    total = rows["counts"].get(status,0) if status else sum(rows["counts"].values())
    return {"items":rows["items"],"counts":rows["counts"],"total":total}


@router.post("/search")
def search_talent(body: CardQuery, status: Status | None = None,
                  auth: AuthContext = Depends(require_recruitment_access)):
    if body.department_id is not None and auth.role != "super_admin":
        raise HTTPException(403, "部门筛选仅供超级管理员使用")
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        return cards(session, auth, body, talent_pool=True, talent_status=status)


@router.get("/{talent_id}")
def talent_detail(talent_id: int, auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        row = session.execute(text(CARD + " WHERE t.id=:id"), {"id":talent_id}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "人才不存在")
        interviews = session.execute(text("""
            SELECT id,scheduled_at,note,created_at FROM talent_interview
            WHERE talent_id=:id ORDER BY scheduled_at DESC
        """), {"id":talent_id}).mappings().all()
    return dict(row, interviews=[dict(record) for record in interviews])


class TalentIn(BaseModel):
    application_id: int = Field(gt=0)


@router.post("", status_code=201)
def add_talent(body: TalentIn, auth: AuthContext = Depends(require_recruitment_access)):
    with owner_engine.begin() as connection:
        departments = fresh_departments(connection, auth)
        row = connection.execute(text("""
            SELECT candidate_id,active_version_id FROM application
            WHERE id=:id AND deleted_at IS NULL AND handling_department_id=ANY(CAST(:deps AS bigint[]))
            FOR SHARE
        """), {"id":body.application_id,"deps":departments}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "简历不存在")
        if row["active_version_id"] is None:
            raise HTTPException(409, "请等待简历解析完成")
        params = {"candidate":row["candidate_id"],"version":row["active_version_id"],
                  "application":body.application_id,"actor":auth.user_id,"deps":departments}
        talent_id = connection.execute(text("""
            INSERT INTO talent(candidate_id,active_version_id,source_application_id,entered_by)
            VALUES(:candidate,:version,:application,:actor)
            ON CONFLICT(candidate_id) DO NOTHING RETURNING id
        """), params).scalar_one_or_none()
        if talent_id is None:
            existing = connection.execute(text("""
                SELECT t.id FROM talent t JOIN application a ON a.id=t.source_application_id
                WHERE t.candidate_id=:candidate AND a.deleted_at IS NULL
                  AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
            """), params).scalar_one_or_none()
            if existing is None:
                raise HTTPException(409, "该简历暂无法转入人才库")
            return {"id":existing,"duplicate":True}
        audit(connection, auth.user_id, "talent_add", talent_id, {}, target_type="talent")
    return {"id":talent_id,"duplicate":False}


class StatusIn(BaseModel):
    status: Status
    status_label: str | None = Field(default=None, max_length=40)


class FollowupIn(BaseModel):
    content: str = Field(default="", max_length=1000)
    status: Status | None = None
    status_label: str | None = Field(default=None, max_length=40)


def _status_label(status, label):
    if status != "custom":
        return None
    label = masked((label or "").strip())
    if not label:
        raise HTTPException(422, "请填写自定义状态")
    return label


def _set_status(connection, talent_id, status, label, auth):
    connection.execute(text("""
        UPDATE talent SET status=:status,status_label=:label,updated_at=now() WHERE id=:id
    """), {"id":talent_id,"status":status,"label":label})
    audit(connection, auth.user_id, "talent_status", talent_id, {"status":status}, target_type="talent")


@router.patch("/{talent_id}/status")
def update_status(talent_id: int, body: StatusIn, auth: AuthContext = Depends(require_recruitment_access)):
    label = _status_label(body.status, body.status_label)
    with owner_engine.begin() as connection:
        _lock_talent(connection, talent_id, auth)
        _set_status(connection, talent_id, body.status, label, auth)
    return {"ok":True}


@router.get("/{talent_id}/followups")
def followups(talent_id: int, before: int | None = Query(default=None, gt=0),
              auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        _ensure_visible(session, talent_id)
        rows = session.execute(text(f"""
            SELECT {FOLLOWUP_FIELDS} FROM talent_followup
            WHERE talent_id=:id AND deleted_at IS NULL
              AND (CAST(:before AS bigint) IS NULL OR id<:before)
            ORDER BY id DESC LIMIT 51
        """), {"id":talent_id,"before":before}).mappings().all()
    return {"items":[dict(record) for record in rows[:50]],"next":rows[49]["id"] if len(rows)>50 else None}


@router.post("/{talent_id}/followups", status_code=201)
def add_followup(talent_id: int, body: FollowupIn, auth: AuthContext = Depends(require_recruitment_access)):
    content = masked(body.content.strip())
    if not content and body.status is None:
        raise HTTPException(422, "请填写跟进内容或选择状态")
    with owner_engine.begin() as connection:
        current = _lock_talent(connection, talent_id, auth)
        status = body.status or current["status"]
        label = _status_label(status, body.status_label) if body.status else current["status_label"]
        if body.status:
            _set_status(connection, talent_id, status, label, auth)
        else:
            connection.execute(text("UPDATE talent SET updated_at=now() WHERE id=:id"), {"id":talent_id})
        record = connection.execute(text(f"""
            INSERT INTO talent_followup(talent_id,author_id,author_name,body,status,status_label)
            VALUES(:id,:author,:name,:body,:status,:label) RETURNING {FOLLOWUP_FIELDS}
        """), {"id":talent_id,"author":auth.user_id,"name":auth.display_name,
               "body":content or "更新了人才状态","status":status,"label":label}).mappings().one()
        audit(connection, auth.user_id, "talent_followup", talent_id, {"followup_id":record["id"]}, target_type="talent")
    return dict(record)


@router.delete("/{talent_id}/followups/{note_id}")
def delete_followup(talent_id: int, note_id: int, auth: AuthContext = Depends(require_recruitment_access)):
    with owner_engine.begin() as connection:
        _lock_talent(connection, talent_id, auth)
        updated = connection.execute(text("""
            UPDATE talent_followup SET deleted_at=now()
            WHERE id=:note AND talent_id=:id AND deleted_at IS NULL
              AND (author_id=:actor OR :admin)
        """), {"note":note_id,"id":talent_id,"actor":auth.user_id,"admin":auth.role=="super_admin"})
        if not updated.rowcount:
            raise HTTPException(404, "跟进不存在或无权删除")
        audit(connection, auth.user_id, "talent_followup_delete", talent_id, {"followup_id":note_id}, target_type="talent")
    return {"ok":True}


class InterviewIn(BaseModel):
    scheduled_at: datetime
    note: str | None = Field(default=None, max_length=1000)


@router.post("/{talent_id}/interview", status_code=201)
def schedule_interview(talent_id: int, body: InterviewIn, auth: AuthContext = Depends(require_recruitment_access)):
    if body.scheduled_at.utcoffset() is None:
        raise HTTPException(422, "面试时间需要明确的时区")
    with owner_engine.begin() as connection:
        _lock_talent(connection, talent_id, auth)
        interview_id = connection.execute(text("""
            INSERT INTO talent_interview(talent_id,scheduled_at,note,created_by)
            VALUES(:id,:when,:note,:actor) RETURNING id
        """), {"id":talent_id,"when":body.scheduled_at,
               "note":masked((body.note or "").strip()) or None,"actor":auth.user_id}).scalar_one()
        connection.execute(text("UPDATE talent SET updated_at=now() WHERE id=:id"), {"id":talent_id})
        audit(connection, auth.user_id, "talent_interview", talent_id, {"interview_id":interview_id}, target_type="talent")
    return {"id":interview_id}
