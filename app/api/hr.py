"""Department HR workflow for scoped applications and evidence."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from unicodedata import normalize
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import ftp_source
from app.core.auth import AuthContext, require_auth, require_recruitment_access
from app.core.cards import CardQuery, cards, education_summary
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked, norm
from app.core.query_embedding import EmbeddingUnavailable
from app.core.recruitment import DEFAULT_ROUNDS, RoundInput, StageCode, expect_revision, rounds_for, save_rounds, stage
from app.core.resume_pipeline import bump_search, insert
from app.core.screening import VISIBLE, SearchInput, read_evidence, search
from app.core.semantic_search import CAPABILITY_THRESHOLD, match_capabilities
from app.core.storage import open_file
from app.core.workflow import (application_lock, audit, cleanup_files, corrected_version,
                               fresh_departments, new_version, purge_application, schedule_parse)

router = APIRouter(prefix="/hr", tags=["HR"])
SAFE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
BASE = f"""
    SELECT a.id,a.candidate_id,a.job_id,coalesce(j.title,'简历库') AS job_title,a.status,a.handling_department_id,
           a.submitted_job_id,a.recruitment_stage,a.interview_round_id,a.workflow_version,ir.name AS interview_round_name,
           a.active_version_id,a.pending_version_id,a.submitted_at,
           v.status AS parse_status,v.extractor_version,v.fail_reason,
           a.submitted_version_id,a.original_url,
           (SELECT p.name FROM resume_profile p JOIN field_usage_policy f ON f.field_key='profile.name'
              AND f.policy_version=st.policy_version AND f.purpose='preview' AND f.allowed
              WHERE p.version_id=a.active_version_id LIMIT 1) AS name
    FROM application a LEFT JOIN job j ON j.id=a.job_id CROSS JOIN search_state st
    LEFT JOIN job_interview_round ir ON ir.id=a.interview_round_id
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
    source_span_no: int | None = Field(default=None, gt=0)


class StageInput(BaseModel):
    workflow_version: int = Field(gt=0)
    code: StageCode
    round_id: int | None = Field(default=None, gt=0)


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class Transfer(Reason):
    job_id: int = Field(gt=0)
    workflow_version: int = Field(gt=0)


class CapabilityTagInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class CapabilityMatchInput(BaseModel):
    query: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00]*$")


class JobInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    jd: str = Field(default="", max_length=4000)
    requirements: str = Field(default="", max_length=4000)
    required_skills: list[Annotated[str, Field(min_length=1,max_length=60)]] = Field(default_factory=list,max_length=20)
    experience_tags: list[Annotated[str, Field(min_length=1,max_length=60)]] = Field(default_factory=list,max_length=12)
    recruitment_start: date | None = None
    recruitment_end: date | None = None
    status: Literal["open", "paused", "closed"] = "open"
    department_id: int = Field(gt=0)
    rounds: list[RoundInput] | None = Field(default=None, max_length=12)
    planned_headcount: int | None = Field(default=None, ge=1, le=100000)
    target_date: date | None = None
    recruitment_type: Literal["social","campus","internship","internal"] = "social"
    recruiter_name: str = Field(default="",max_length=80)
    recruiter_user_id: int | None = Field(default=None, gt=0)
    source_category: str = Field(default="", max_length=60)


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
def options(auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        departments = s.execute(text("SELECT id,name FROM department WHERE status='active' ORDER BY id")).mappings().all()
        skills = s.execute(text("SELECT name FROM skill ORDER BY name")).scalars().all()
        tags = s.execute(text("""
            SELECT job_id,tag_code FROM job_school_filter ORDER BY job_id,tag_code
        """)).mappings().all()
        capabilities = s.execute(text("""
            SELECT code,name FROM capability_group ORDER BY display_order,code
        """)).mappings().all()
    return {"departments": [dict(x) for x in departments], "hr_department_ids": auth.departments, "skills": skills,
            "school_tags": [dict(x) for x in tags], "capabilities": [dict(x) for x in capabilities]}


def _require_capability_filter_access(connection):
    allowed = connection.execute(text("""
        SELECT 1 FROM search_state s
        JOIN field_usage_policy p ON p.policy_version=s.policy_version
        JOIN field_definition f USING (field_key)
        WHERE s.singleton AND p.field_key='skill.name' AND p.purpose='filter' AND p.allowed AND f.status='active'
    """)).scalar_one_or_none()
    if allowed is None:
        raise HTTPException(403, "能力标签筛选未获授权")


def _recent_capability_tags(connection):
    return {"items": [dict(row) for row in connection.execute(text("""
        SELECT code,name FROM capability_group
        ORDER BY created_at DESC NULLS LAST,display_order,code LIMIT 10
    """)).mappings()]}


@router.get("/capability-tags")
def list_capability_tags(full: bool = False, auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as session:
        _require_capability_filter_access(session)
        if full:
            return {"items": [dict(row) for row in session.execute(text("""
                SELECT code,name FROM capability_group ORDER BY display_order,code
            """)).mappings()]}
        return _recent_capability_tags(session)


@router.post("/capability-tags/match")
def match_capability_tags(body: CapabilityMatchInput, auth: AuthContext = Depends(require_recruitment_access)):
    value = norm(body.query)
    with _scope(auth) as session:
        _require_capability_filter_access(session)
        if not value:
            return {"items": [], "threshold": CAPABILITY_THRESHOLD}
        try:
            return match_capabilities(session, value)
        except EmbeddingUnavailable as exc:
            raise HTTPException(503, "能力标签匹配暂不可用，请稍后重试") from exc


@router.post("/capability-tags", status_code=201)
def create_capability_tag(body: CapabilityTagInput, auth: AuthContext = Depends(require_recruitment_access)):
    name = " ".join(normalize("NFKC", body.name).split())
    if not name or len(name) > 40 or not name.isprintable():
        raise HTTPException(422, "请输入 1 至 40 个字符的标签名称")
    with owner_engine.begin() as connection:
        if not fresh_departments(connection, auth) and auth.role != "super_admin":
            raise HTTPException(403, "需要招聘管理权限")
        _require_capability_filter_access(connection)
        code = connection.execute(text("""
            INSERT INTO capability_group(code,name,domain,display_order)
            VALUES (:code,:name,'general',100)
            ON CONFLICT(name) DO NOTHING RETURNING code
        """), {"code": f"custom_{uuid4().hex}", "name": name}).scalar_one_or_none()
        if code is None:
            raise HTTPException(409, "该能力标签已存在")
        audit(connection, auth.user_id, "capability_tag_created", code, target_type="capability_group")
        return _recent_capability_tags(connection)


@router.get("/applications")
def applications(job_id: int | None = None, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                 auth: AuthContext = Depends(require_recruitment_access)):
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
def detail(application_id: int, include_evidence: bool = True, auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        version = app["active_version_id"]
        policy = s.execute(text("SELECT policy_version FROM search_state")).scalar_one()
        fields = read_evidence(s, version, policy) if version and include_evidence else []
        issues = _issues(s, version, policy) if include_evidence else []
        reviewed_extensions = [dict(row) for row in s.execute(text("""
            SELECT x.record_no,x.raw_label,coalesce(x.payload->>'corrected',x.payload->>'raw') AS value
            FROM resume_extra_value x JOIN resume_source_span s USING(version_id,span_no)
            WHERE x.version_id=:v AND x.reviewed AND x.field_key IS NULL AND NOT s.is_sensitive
            ORDER BY x.record_no
        """), {"v":version}).mappings()] if version and include_evidence else []
        education = education_summary(s, version, policy)
        rounds = rounds_for(s, app["job_id"]) if app["job_id"] is not None else []
        current = stage(app["recruitment_stage"], app["interview_round_id"], app["interview_round_name"])
        document = s.execute(text("""
            SELECT v.preview_key IS NOT NULL AS has_preview, d.mime_type
            FROM resume_version v JOIN resume_document d ON d.id=v.document_id WHERE v.id=:v
        """), {"v": version}).mappings().one_or_none()
        audit(s, auth.user_id, "view_resume_detail", application_id, {"version_id": version})
    return {**app, "evidence": fields, "issues": issues, "reviewed_extensions":reviewed_extensions,
            "education": education, "stage": current, "rounds": rounds,
            "has_preview": bool(document and document["has_preview"]),
            "original_mime_type": document["mime_type"] if document else None}


@router.post("/resume-cards/search")
def resume_cards(body: CardQuery, auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as session:
        return cards(session, auth, body, card_matches=True)


def _rendered_image(session, key):
    if not key:
        raise HTTPException(404, "脱敏预览不可用")
    try:
        return FileResponse(open_file(key), media_type="image/png", headers=SAFE_HEADERS)
    except FileNotFoundError as exc:
        raise HTTPException(404, "脱敏预览不可用，请重新解析") from exc


@router.get("/applications/{application_id}/preview")
def preview(application_id: int, page: int = Query(1, ge=1, le=40), version: int | None = Query(None, gt=0),
            auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        current = app["active_version_id"]
        if version is not None and current != version:
            raise HTTPException(409, "简历版本已更新，请刷新")
        key = s.execute(text("""
            SELECT coalesce((SELECT CASE WHEN pp.photo_object_key IS NOT NULL AND EXISTS (
                    SELECT 1 FROM field_usage_policy p JOIN search_state st USING(policy_version)
                    JOIN field_definition f USING(field_key)
                    WHERE p.field_key='profile.avatar' AND p.purpose='preview' AND p.allowed AND f.status='active'
                ) THEN pp.photo_object_key ELSE pp.object_key END
                FROM resume_preview_page pp WHERE pp.version_id=:v AND pp.page_no=:page),
                            CASE WHEN :page=1 THEN v.preview_key END)
            FROM resume_version v WHERE v.id=:v
        """), {"v": current, "page": page}).scalar_one_or_none()
        result = _rendered_image(s, key)
        if page == 1:
            audit(s, auth.user_id, "view_resume_preview", application_id, {"version_id": current})
    return result


@router.get("/applications/{application_id}/avatar")
def avatar(application_id: int, version: int | None = Query(None, gt=0), auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        app = _get(s, auth, application_id)
        current = app["active_version_id"]
        if version is not None and current != version:
            raise HTTPException(409, "简历版本已更新，请刷新")
        key = s.execute(text("""
            SELECT v.avatar_key FROM resume_version v CROSS JOIN search_state st
            JOIN field_usage_policy p ON p.policy_version=st.policy_version
              AND p.field_key='profile.avatar' AND p.purpose='preview' AND p.allowed
            JOIN field_definition f ON f.field_key=p.field_key AND f.status='active'
            WHERE v.id=:v AND st.singleton
        """), {"v": current}).scalar_one_or_none()
        return _rendered_image(s, key)


@router.get("/applications/{application_id}/original")
def original(application_id: int, inline: bool = False, version: int | None = Query(None, gt=0),
             auth: AuthContext = Depends(require_auth)):
    with owner_engine.begin() as connection:
        if auth.role == "dept_hr" or inline:
            application_lock(connection, application_id, auth)
        params = {"a": application_id, "u": auth.user_id}
        document = connection.execute(text("""
            SELECT a.active_version_id,d.object_key,d.mime_type,d.source_kind,d.original_filename,
                   v.document_id=submitted.document_id AND EXISTS (
                     SELECT 1 FROM audit_log log WHERE log.actor_id=:u AND log.action='resume_import'
                       AND log.target_type='application' AND log.target_id=CAST(a.id AS text)
                   ) AS uploaded_by_viewer
            FROM application a JOIN resume_version v ON v.id=coalesce(a.active_version_id,a.submitted_version_id)
            JOIN resume_version submitted ON submitted.id=a.submitted_version_id
            JOIN resume_document d ON d.id=v.document_id
            WHERE a.id=:a AND a.deleted_at IS NULL
        """), params).mappings().one_or_none()
        if document is None:
            raise HTTPException(404, "原件不可访问或授权已过期")
        # Reconstructed files contain the approved display copy and follow the application scope.
        # Raw uploads retain the uploader preview or one-use grant rules.
        reconstructed = document["source_kind"] == "reconstructed"
        if reconstructed and not inline and auth.role != "dept_hr":
            application_lock(connection, application_id, auth)
        uses_grant = not (reconstructed or inline and document["uploaded_by_viewer"])
        if uses_grant and connection.execute(text("""
            SELECT 1 FROM resume_original_grant WHERE application_id=:a AND user_id=:u
              AND expires_at>now() FOR UPDATE
        """), params).first() is None:
            raise HTTPException(404, "原件不可访问或授权已过期")
        if version is not None and document["active_version_id"] != version:
            raise HTTPException(409, "简历版本已更新，请刷新")
        if inline and document["mime_type"] != "application/pdf":
            raise HTTPException(415, "仅支持直接预览 PDF 原件")
        try:
            path = open_file(document["object_key"])
        except FileNotFoundError as exc:
            raise HTTPException(404, "原件不可用") from exc
        if uses_grant:
            connection.execute(text("DELETE FROM resume_original_grant WHERE application_id=:a AND user_id=:u"), params)
        action = "view_original_pdf" if inline else "download_original"
        audit(connection, auth.user_id, action, application_id, {"version_id": document["active_version_id"]})
    if inline:
        return FileResponse(path, media_type="application/pdf", filename=document["original_filename"] or f"resume-{application_id}.pdf",
                            content_disposition_type="inline", headers=SAFE_HEADERS)
    filename = document["original_filename"] or f"resume-{application_id}." + path.rsplit(".", 1)[-1]
    return FileResponse(path, media_type="application/octet-stream", filename=filename,
                        headers=SAFE_HEADERS)


@router.get("/review")
def review_queue(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                 job_id: int | None = Query(default=None, gt=0), department_id: int | None = Query(default=None, gt=0),
                 auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        rows = s.execute(text(BASE + """
            AND (CAST(:job AS bigint) IS NULL OR a.job_id=:job)
            AND (CAST(:department AS bigint) IS NULL OR a.handling_department_id=:department)
            AND (EXISTS (SELECT 1 FROM resume_field_issue i JOIN field_usage_policy p USING(field_key)
                 WHERE i.version_id=a.active_version_id AND p.policy_version=st.policy_version
                   AND p.purpose='preview' AND p.allowed)
                OR EXISTS (SELECT 1 FROM resume_extra_value x WHERE x.version_id=a.active_version_id AND NOT x.reviewed AND x.field_key IS NULL))
            ORDER BY a.id DESC LIMIT :n OFFSET :offset
        """), {"deps": auth.departments, "n": limit+1, "offset": offset,"job":job_id,"department":department_id}).mappings().all()
    return {"items": [dict(r) for r in rows[:limit]], "has_more": len(rows)>limit}


@router.post("/applications/{application_id}/corrections")
def correct(application_id: int, body: Correction, auth: AuthContext = Depends(require_recruitment_access)):
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
def reparse(application_id: int, auth: AuthContext = Depends(require_recruitment_access)):
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


@router.put("/applications/{application_id}/stage")
def change_stage(application_id: int, body: StageInput, auth: AuthContext = Depends(require_recruitment_access)):
    if (body.code == "interview") != (body.round_id is not None):
        raise HTTPException(422, "面试状态必须选择当前岗位的轮次")
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        expect_revision(row, body.workflow_version)
        # Serialize with edits to this job's interview plan.
        cn.execute(text("SELECT id FROM job WHERE id=:j FOR SHARE"), {"j": row["job_id"]})
        rounds = {item["id"]: item["name"] for item in rounds_for(cn, row["job_id"])}
        if body.round_id is not None and body.round_id not in rounds:
            raise HTTPException(422, "面试轮次不属于当前岗位")
        result = stage(body.code, body.round_id, rounds.get(body.round_id))
        old = stage(row["recruitment_stage"], row["interview_round_id"], rounds.get(row["interview_round_id"]))
        if result == old:
            return {"stage": result, "workflow_version": row["workflow_version"]}
        version = cn.execute(text("""
            UPDATE application SET recruitment_stage=:code,interview_round_id=:round
            WHERE id=:a RETURNING workflow_version
        """), {"a": application_id, "code": body.code, "round": body.round_id}).scalar_one()
        audit(cn, auth.user_id, "recruitment_stage_change", application_id,
              {"job_id": row["job_id"], "from": old, "to": result})
        bump_search(cn)
    return {"stage": result, "workflow_version": version}


@router.get("/applications/{application_id}/transfer-options")
def transfer_options(application_id: int, auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as s:
        row = _get(s, auth, application_id)
        items = s.execute(text("""
            SELECT j.id,j.title,j.department_id,d.name AS department_name
            FROM job j JOIN department d ON d.id=j.department_id
            WHERE j.status='open' AND NOT j.hidden AND d.status='active' AND j.id<>:j
            ORDER BY (j.department_id=:d) DESC,d.name,j.title,j.id
        """), {"j": row["job_id"], "d": row["handling_department_id"]}).mappings().all()
    return {"items": [dict(item) for item in items]}


@router.post("/applications/{application_id}/transfer")
def transfer(application_id: int, body: Transfer, auth: AuthContext = Depends(require_recruitment_access)):
    with owner_engine.begin() as cn:
        row = application_lock(cn, application_id, auth)
        expect_revision(row, body.workflow_version)
        if row["job_id"] == body.job_id:
            raise HTTPException(422, "请选择其他岗位")
        target = cn.execute(text("""
            SELECT j.department_id FROM job j JOIN department d ON d.id=j.department_id
            WHERE j.id=:j AND j.status='open' AND NOT j.hidden AND d.status='active' FOR SHARE OF j,d
        """), {"j": body.job_id}).scalar_one_or_none()
        if target is None:
            raise HTTPException(404, "目标岗位不存在或未开放")
        cn.execute(text("SELECT id FROM candidate WHERE id=:c FOR UPDATE"), {"c": row["candidate_id"]})
        if cn.execute(text("""
            SELECT 1 FROM application WHERE candidate_id=:c AND job_id=:j AND deleted_at IS NULL LIMIT 1
        """), {"c": row["candidate_id"], "j": body.job_id}).first():
            raise HTTPException(409, "无法调剂：该候选人在目标岗位已有申请")
        reason = _note(body.reason)
        version = cn.execute(text("""
            UPDATE application SET job_id=:j,handling_department_id=:d,
                recruitment_stage='review',interview_round_id=NULL
            WHERE id=:a RETURNING workflow_version
        """), {"j": body.job_id, "d": target, "a": application_id}).scalar_one()
        cn.execute(text("DELETE FROM resume_original_grant WHERE application_id=:a"), {"a": application_id})
        audit(cn, auth.user_id, "application_transfer", application_id, {
            "from_job_id": row["job_id"], "to_job_id": body.job_id, "from_department_id": row["handling_department_id"],
            "to_department_id": target, "from_stage": row["recruitment_stage"], "from_round_id": row["interview_round_id"],
            "to_stage": "review", "reason": reason,
        })
        bump_search(cn)
    return {"job_id": body.job_id, "stage": stage("review"), "workflow_version": version, "remains_visible": target in auth.departments}


@router.post("/applications/{application_id}/purge")
def purge(application_id: int, body: Reason, auth: AuthContext = Depends(require_recruitment_access)):
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
def search_candidates(body: SearchInput, auth: AuthContext = Depends(require_recruitment_access)):
    with _scope(auth) as session:
        return search(session, auth, body)


@router.post("/jobs", status_code=201)
@router.put("/jobs/{job_id}")
def write_job(body: JobInput, job_id: int | None = None, auth: AuthContext = Depends(require_auth)):
    if job_id is None and ftp_source.enabled():
        raise HTTPException(409, "请在岗位管理的子目录中创建岗位")
    with owner_engine.begin() as cn:
        departments = fresh_departments(cn, auth)
        department_active = cn.execute(text("SELECT 1 FROM department WHERE id=:id AND status='active' FOR SHARE"),
                                       {"id":body.department_id}).first()
        if body.department_id not in departments or (job_id is None and not department_active):
            raise HTTPException(404, "部门不存在")
        params = {**body.model_dump(exclude={"rounds"}), "id":job_id, "deps":departments, "title":body.title.strip()}
        if not params["title"]:
            raise HTTPException(422, "请输入岗位名称")
        for key in ("required_skills","experience_tags"):
            values = {}
            for value in params[key]:
                label = value.strip()
                if label:
                    values.setdefault(label.casefold(),label)
            params[key] = list(values.values())
        previous = None
        if job_id is not None:
            previous = cn.execute(text("""
                SELECT department_id,recruitment_start,recruitment_end,recruiter_user_id FROM job
                WHERE id=:id AND department_id=ANY(CAST(:deps AS bigint[])) FOR UPDATE
            """), params).mappings().one_or_none()
            if previous is None:
                raise HTTPException(404, "岗位不存在")
            if previous["department_id"] != body.department_id:
                raise HTTPException(422, "已创建的岗位不能更换部门，请新建岗位后调剂")
        changed_recruiter = body.recruiter_user_id is not None and (
            previous is None or previous["recruiter_user_id"] != body.recruiter_user_id
        )
        if changed_recruiter and not cn.execute(text("""
            SELECT 1 FROM department_admin da JOIN app_user u ON u.id=da.user_id
            WHERE u.id=:recruiter_user_id AND u.status='active'
              AND (:admin OR da.department_id=:department_id)
            FOR SHARE OF da,u
        """), {**params, "admin": auth.role == "super_admin"}).first():
            raise HTTPException(422, "请选择权限范围内的有效招聘人")
        start, end = (params[key] if not previous or key in body.model_fields_set else previous[key]
                      for key in ("recruitment_start","recruitment_end"))
        if start and end and start > end:
            raise HTTPException(422,"招聘截止日期不能早于开始日期")
        if job_id is None:
            job_id = cn.execute(text("""
                INSERT INTO job(department_id,title,jd,status,planned_headcount,target_date,recruitment_type,recruiter_name,
                                requirements,required_skills,experience_tags,recruitment_start,recruitment_end,recruiter_user_id,
                                source_category)
                VALUES(:department_id,:title,:jd,:status,:planned_headcount,:target_date,:recruitment_type,:recruiter_name,
                       :requirements,:required_skills,:experience_tags,:recruitment_start,:recruitment_end,:recruiter_user_id,
                       nullif(:source_category,'')) RETURNING id
            """), params).scalar_one()
            save_rounds(cn, job_id, body.rounds if body.rounds is not None else [RoundInput(name=name) for name in DEFAULT_ROUNDS])
        else:
            editable = ("planned_headcount","target_date","recruitment_type","recruiter_name","recruiter_user_id","requirements",
                        "required_skills","experience_tags","recruitment_start","recruitment_end")
            fields = [key for key in editable if key in body.model_fields_set]
            assignments = "".join(f",{key}=:{key}" for key in fields)
            if "source_category" in body.model_fields_set:
                assignments += ",source_category=nullif(:source_category,'')"
            cn.execute(text(f"UPDATE job SET title=:title,jd=:jd,status=:status,updated_at=now(){assignments} WHERE id=:id"), params)
            if body.rounds is not None:
                save_rounds(cn, job_id, body.rounds)
        audit(cn, auth.user_id, "job_write", job_id, target_type="job")
        bump_search(cn)
    return {"id": job_id}
