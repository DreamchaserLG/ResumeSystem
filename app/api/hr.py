"""Department HR workflow for scoped applications and evidence."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth, require_dept_hr
from app.core.cards import CardQuery, cards
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked
from app.core.resume_pipeline import bump_search, insert
from app.core.screening import VISIBLE, SearchInput, read_evidence, search
from app.core.storage import open_file
from app.core.workflow import (application_lock, audit, cleanup_files, corrected_version,
                               fresh_departments, new_version, purge_application, schedule_parse)

router = APIRouter(prefix="/hr", tags=["HR"])
SAFE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
BASE = f"""
    SELECT a.id,a.candidate_id,a.job_id,j.title AS job_title,a.status,a.handling_department_id,
           a.active_version_id,a.pending_version_id,a.submitted_at,
           v.status AS parse_status,v.extractor_version,v.fail_reason,
           a.submitted_version_id,
           (SELECT p.name FROM resume_profile p JOIN field_usage_policy f ON f.field_key='profile.name'
              AND f.policy_version=st.policy_version AND f.purpose='preview' AND f.allowed
              WHERE p.version_id=a.active_version_id LIMIT 1) AS name
    FROM application a JOIN job j ON j.id=a.job_id CROSS JOIN search_state st
    LEFT JOIN resume_version v ON v.id=coalesce(a.pending_version_id,a.active_version_id,a.submitted_version_id)
    WHERE {VISIBLE} AND st.singleton
"""


class Correction(BaseModel):
    version_id: int = Field(gt=0)
    record_no: int = Field(gt=0)
    field_key: str = Field(max_length=120)
    action: Literal["correct", "dismiss"]
    value: str | None = Field(default=None, max_length=2000)
    reason: str = Field(min_length=1, max_length=300)


class Decision(BaseModel):
    version_id: int = Field(gt=0)
    decision: Literal["advance", "reject", "hold"]
    comment: str = Field(min_length=1, max_length=1000)


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class Transfer(Reason):
    department_id: int = Field(gt=0)


class JobInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    jd: str = Field(default="", max_length=4000)
    status: Literal["open", "paused", "closed"] = "open"
    department_id: int = Field(gt=0)


def _note(value):
    if not value.strip():
        raise HTTPException(422, "请填写处理说明")
    return masked(value.strip())


def _scope(auth):
    return rls_session(departments=auth.departments, user_id=auth.user_id)


def _get(session, auth, application_id):
    row = session.execute(text(BASE + " AND a.id=:id"), {"id": application_id, "deps": auth.departments}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "申请不存在")
    return dict(row)


@router.get("/options")
def options(auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        departments = s.execute(text("SELECT id,name FROM department WHERE status='active' ORDER BY id")).mappings().all()
        skills = s.execute(text("SELECT name FROM skill ORDER BY name")).scalars().all()
        tags = s.execute(text("""
            SELECT job_id,tag_code FROM job_school_filter ORDER BY job_id,tag_code
        """)).mappings().all()
    return {"departments": [dict(x) for x in departments], "hr_department_ids": auth.departments, "skills": skills, "school_tags": [dict(x) for x in tags]}


@router.get("/applications")
def applications(job_id: int | None = None, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                 auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        rows = s.execute(text(BASE + """
            AND (CAST(:j AS bigint) IS NULL OR a.job_id=:j) ORDER BY a.id DESC LIMIT :count OFFSET :offset
        """), {"deps": auth.departments, "j": job_id, "count": limit+1, "offset": offset}).mappings().all()
    return {"items": [dict(r) for r in rows[:limit]], "has_more": len(rows)>limit}


def _issues(session, version, policy):
    if version is None:
        return []
    return [dict(row) for row in session.execute(text("""
        SELECT i.record_no,i.field_key,i.reason,i.span_no,coalesce(f.display_name,i.field_key) AS label,
          CASE WHEN NOT s.is_sensitive AND s.span_kind<>'photo' THEN s.raw_text END AS evidence,
          CASE WHEN NOT s.is_sensitive THEN s.locator END AS locator,s.ocr_confidence
        FROM resume_field_issue i LEFT JOIN resume_source_span s USING(version_id,span_no)
        JOIN field_definition f ON f.field_key=i.field_key AND f.status='active'
        JOIN field_usage_policy p ON p.field_key=f.field_key AND p.purpose='preview' AND p.allowed AND p.policy_version=:p
        WHERE i.version_id=:v
        UNION ALL
        SELECT x.record_no,'extension','unregistered',x.span_no,x.raw_label,
               CASE WHEN NOT s.is_sensitive AND s.span_kind<>'photo' THEN x.payload->>'raw' END,
               CASE WHEN NOT s.is_sensitive THEN s.locator END,s.ocr_confidence
        FROM resume_extra_value x JOIN resume_source_span s USING(version_id,span_no)
        WHERE x.version_id=:v AND x.field_key IS NULL AND NOT x.reviewed AND NOT s.is_sensitive
        ORDER BY record_no,field_key
    """), {"v": version, "p": policy}).mappings()]


@router.get("/applications/{application_id}")
def detail(application_id: int, auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        version = app["active_version_id"]
        policy = s.execute(text("SELECT policy_version FROM search_state")).scalar_one()
        fields = read_evidence(s, version, policy) if version else []
        issues = _issues(s, version, policy)
        reviews = s.execute(text("""
            SELECT decision,comment,created_at,resume_version_id FROM application_review
            WHERE application_id=:a ORDER BY id DESC LIMIT 40
        """), {"a": application_id}).mappings().all()
        preview = s.execute(text("""
            SELECT preview_key IS NOT NULL FROM resume_version WHERE id=:v
        """), {"v": version}).scalar_one_or_none()
        audit(s, auth.user_id, "view_resume_detail", application_id, {"version_id": version})
    return {**app, "evidence": fields, "issues": issues, "reviews": [dict(r) for r in reviews], "has_preview": bool(preview)}


@router.post("/resume-cards/search")
def resume_cards(body: CardQuery, auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as session:
        return cards(session, auth, body)


def _rendered_image(session, key):
    if not key:
        raise HTTPException(404, "脱敏预览不可用")
    try:
        return FileResponse(open_file(key), media_type="image/png", headers=SAFE_HEADERS)
    except FileNotFoundError as exc:
        raise HTTPException(404, "脱敏预览不可用，请重新解析") from exc


@router.get("/applications/{application_id}/preview")
def preview(application_id: int, page: int = Query(1, ge=1, le=40), version: int | None = Query(None, gt=0),
            auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        current = app["active_version_id"]
        if version is not None and current != version:
            raise HTTPException(409, "简历版本已更新，请刷新")
        key = s.execute(text("""
            SELECT coalesce((SELECT object_key FROM resume_preview_page WHERE version_id=:v AND page_no=:page),
                            CASE WHEN :page=1 THEN v.preview_key END)
            FROM resume_version v WHERE v.id=:v
        """), {"v": current, "page": page}).scalar_one_or_none()
        result = _rendered_image(s, key)
        if page == 1:
            audit(s, auth.user_id, "view_resume_preview", application_id, {"version_id": current})
    return result


@router.get("/applications/{application_id}/avatar")
def avatar(application_id: int, version: int | None = Query(None, gt=0), auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        current = app["active_version_id"]
        if version is not None and current != version:
            raise HTTPException(409, "简历版本已更新，请刷新")
        key = s.execute(text("""
            SELECT v.avatar_key FROM resume_version v CROSS JOIN search_state st
            JOIN field_usage_policy p ON p.policy_version=st.policy_version
              AND p.field_key='profile.avatar' AND p.purpose='preview' AND p.allowed
            WHERE v.id=:v AND st.singleton
        """), {"v": current}).scalar_one_or_none()
        return _rendered_image(s, key)


@router.get("/applications/{application_id}/original")
def original(application_id: int, auth: AuthContext = Depends(require_auth)):
    with owner_engine.begin() as connection:
        if auth.role == "dept_hr":
            application_lock(connection, application_id, auth)
        grant = connection.execute(text("""
            SELECT a.active_version_id,d.object_key,d.mime_type
            FROM application a JOIN resume_version v ON v.id=coalesce(a.active_version_id,a.submitted_version_id)
            JOIN resume_document d ON d.id=v.document_id
            JOIN resume_original_grant g ON g.application_id=a.id AND g.user_id=:u
            WHERE a.id=:a AND a.deleted_at IS NULL AND g.expires_at>now() FOR UPDATE OF g
        """), {"a": application_id, "u": auth.user_id}).mappings().one_or_none()
        if grant is None:
            raise HTTPException(404, "原件不可访问或授权已过期")
        try:
            path = open_file(grant["object_key"])
        except FileNotFoundError as exc:
            raise HTTPException(404, "原件不可用") from exc
        connection.execute(text("DELETE FROM resume_original_grant WHERE application_id=:a AND user_id=:u"),
                           {"a": application_id, "u": auth.user_id})
        audit(connection, auth.user_id, "download_original", application_id, {"version_id": grant["active_version_id"]})
    return FileResponse(path, media_type="application/octet-stream", filename=f"resume-{application_id}." + path.rsplit(".", 1)[-1],
                        headers=SAFE_HEADERS)


@router.get("/review")
def review_queue(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                 auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as s:
        rows = s.execute(text(BASE + """
            AND (EXISTS (SELECT 1 FROM resume_field_issue i JOIN field_usage_policy p USING(field_key)
                 WHERE i.version_id=a.active_version_id AND p.policy_version=st.policy_version
                   AND p.purpose='preview' AND p.allowed)
                OR EXISTS (SELECT 1 FROM resume_extra_value x WHERE x.version_id=a.active_version_id AND NOT x.reviewed AND x.field_key IS NULL))
            ORDER BY a.id DESC LIMIT :n OFFSET :offset
        """), {"deps": auth.departments, "n": limit+1, "offset": offset}).mappings().all()
    return {"items": [dict(r) for r in rows[:limit]], "has_more": len(rows)>limit}


@router.post("/applications/{application_id}/corrections")
def correct(application_id: int, body: Correction, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        permitted = cn.execute(text("""
            SELECT 1 FROM field_usage_policy f JOIN search_state st USING(policy_version)
            JOIN field_definition d USING(field_key)
            WHERE f.field_key=:field AND f.purpose='preview' AND f.allowed AND d.status='active'
        """), {"field": body.field_key}).first()
        if body.field_key != "extension" and not permitted:
            raise HTTPException(403, "此字段未获复核授权")
        version = corrected_version(cn, row, body)
        audit(cn, auth.user_id, "field_review", application_id,
              {"version_id": version, "field": body.field_key, "action": body.action, "reason": _note(body.reason)})
    return {"version_id": version}


@router.post("/applications/{application_id}/reparse", status_code=202)
def reparse(application_id: int, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        target = row["pending_version_id"] or row["active_version_id"] or row["submitted_version_id"]
        status = cn.execute(text("SELECT status FROM resume_version WHERE id=:v"), {"v": target}).scalar_one()
        if status == "parsing":
            raise HTTPException(409, "已有解析任务")
        version = new_version(cn, target, "parsing")
        cn.execute(text("UPDATE application SET pending_version_id=:v WHERE id=:a"), {"v": version, "a": application_id})
        audit(cn, auth.user_id, "resume_reparse", application_id, {"version_id": version})
    schedule_parse(version)
    return {"version_id": version, "status": "parsing"}


@router.post("/applications/{application_id}/decisions")
def decision(application_id: int, body: Decision, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        if row["active_version_id"] != body.version_id:
            raise HTTPException(409, "简历版本已变化")
        insert(cn, "application_review", [{"application_id": application_id, "resume_version_id": body.version_id,
              "author_id": auth.user_id, "decision": body.decision, "comment": _note(body.comment)}])
        status = {"advance": "active", "reject": "closed", "hold": "on_hold"}[body.decision]
        cn.execute(text("UPDATE application SET status=:s WHERE id=:a"), {"s": status, "a": application_id})
        audit(cn, auth.user_id, "recruitment_decision", application_id, {"decision": body.decision, "version_id": body.version_id})
        bump_search(cn)
    return {"status": status}


@router.post("/applications/{application_id}/transfer")
def transfer(application_id: int, body: Transfer, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        if not cn.execute(text("SELECT 1 FROM department WHERE id=:d AND status='active'"), {"d": body.department_id}).first():
            raise HTTPException(422, "目标部门不可用")
        cn.execute(text("UPDATE application SET handling_department_id=:d WHERE id=:a"), {"d": body.department_id, "a": application_id})
        cn.execute(text("DELETE FROM resume_original_grant WHERE application_id=:a"), {"a": application_id})
        audit(cn, auth.user_id, "application_transfer", application_id,
              {"from": row["handling_department_id"], "to": body.department_id, "reason": _note(body.reason)})
        bump_search(cn)
    return {"ok": True}


@router.post("/applications/{application_id}/purge")
def purge(application_id: int, body: Reason, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        if row["pending_version_id"] is not None and cn.execute(
            text("SELECT status FROM resume_version WHERE id=:v"), {"v": row["pending_version_id"]}
        ).scalar_one() == "parsing":
            raise HTTPException(409, "解析期间无法清理申请")
        if row["active_version_id"] is None and cn.execute(
            text("SELECT status FROM resume_version WHERE id=:v"), {"v": row["submitted_version_id"]}
        ).scalar_one() == "parsing":
            raise HTTPException(409, "解析期间无法清理申请")
        purge_application(cn, row, auth.user_id, _note(body.reason))
    with owner_engine.begin() as cn:
        remaining = cleanup_files(cn)
    return {"ok": True, "cleanup_pending": remaining}


@router.post("/search")
def search_candidates(body: SearchInput, auth: AuthContext = Depends(require_dept_hr)):
    with _scope(auth) as session:
        return search(session, auth, body)


@router.post("/jobs", status_code=201)
@router.put("/jobs/{job_id}")
def write_job(body: JobInput, job_id: int | None = None, auth: AuthContext = Depends(require_dept_hr)):
    with owner_engine.begin() as cn:
        departments = fresh_departments(cn, auth)
        if body.department_id not in departments:
            raise HTTPException(404, "部门不存在")
        params = {**body.model_dump(), "id": job_id, "deps": departments, "title": body.title.strip()}
        if not params["title"]:
            raise HTTPException(422, "请输入岗位名称")
        if job_id is None:
            job_id = cn.execute(text("""
                INSERT INTO job(department_id,title,jd,status) VALUES(:department_id,:title,:jd,:status) RETURNING id
            """), params).scalar_one()
        else:
            updated = cn.execute(text("""
                UPDATE job SET department_id=:department_id,title=:title,jd=:jd,status=:status
                WHERE id=:id AND department_id=ANY(CAST(:deps AS bigint[])) RETURNING id
            """), params).scalar_one_or_none()
            if updated is None:
                raise HTTPException(404, "岗位不存在")
        audit(cn, auth.user_id, "job_write", job_id, target_type="job")
        bump_search(cn)
    return {"id": job_id}
