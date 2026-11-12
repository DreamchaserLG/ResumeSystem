"""超管管理接口：账号、部门 HR 授权/撤权、审计查询。

授权变更（grant/revoke）必须 bump_perm_version（02 §3）并写审计。
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.audit import write_audit
from app.core.auth import AuthContext, require_super_admin
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked, norm
from app.core.integration import PRIVATE_FIELD
from app.core.resume_pipeline import build_projection, bump_search, insert
from app.core.workflow import audit, cleanup_files
from app.core.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserIn(BaseModel):
    display_name: str = Field(min_length=1,max_length=80)
    email: str = Field(min_length=3,max_length=160)
    password: str = Field(min_length=8,max_length=128)


class GrantIn(BaseModel):
    user_id: int
    department_id: int


@router.post("/users")
def create_user(body: CreateUserIn, auth: AuthContext = Depends(require_super_admin)):
    # 超管上下文事务：app_user 的写策略要求 app_is_super_admin() 为真
    with rls_session(user_id=auth.user_id) as s:
        exists = s.execute(
            text("SELECT 1 FROM app_user WHERE email = :e"), {"e": body.email.strip().lower()}
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="邮箱已存在")
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="密码至少 8 位")
        row = s.execute(
            text(
                "INSERT INTO app_user (display_name, email, password_hash)"
                " VALUES (:n, :e, :p) RETURNING id"
            ),
            {
                "n": body.display_name,
                "e": body.email.strip().lower(),
                "p": hash_password(body.password),
            },
        ).fetchone()
        new_id = row.id
    write_audit(auth.user_id, "user_create", "user", body.email, detail={"new_user_id": new_id})
    return {"user_id": new_id}


@router.post("/grant-dept-hr")
def grant_dept_hr(body: GrantIn, auth: AuthContext = Depends(require_super_admin)):
    with rls_session(user_id=auth.user_id) as s:
        dept = s.execute(
            text("SELECT name FROM department WHERE id = :d"), {"d": body.department_id}
        ).fetchone()
        if dept is None:
            raise HTTPException(status_code=404, detail="部门不存在")
        user = s.execute(
            text("SELECT email FROM app_user WHERE id = :u"), {"u": body.user_id}
        ).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        s.execute(
            text(
                "INSERT INTO department_admin (department_id, user_id, granted_by)"
                " VALUES (:d, :u, :g) ON CONFLICT DO NOTHING"
            ),
            {"d": body.department_id, "u": body.user_id, "g": auth.user_id},
        )
        s.execute(text("UPDATE app_user SET perm_version=perm_version+1 WHERE id=:u"), {"u": body.user_id})
        audit(s, auth.user_id, "permission_grant", body.user_id, {"department_id": body.department_id}, "user")
    return {"ok": True}


@router.post("/revoke-dept-hr")
def revoke_dept_hr(body: GrantIn, auth: AuthContext = Depends(require_super_admin)):
    with rls_session(user_id=auth.user_id) as s:
        s.execute(
            text("DELETE FROM department_admin WHERE department_id = :d AND user_id = :u"),
            {"d": body.department_id, "u": body.user_id},
        )
        s.execute(text("UPDATE app_user SET perm_version=perm_version+1 WHERE id=:u"), {"u": body.user_id})
        audit(s, auth.user_id, "permission_revoke", body.user_id, {"department_id": body.department_id}, "user")
    return {"ok": True}


@router.get("/audit")
def query_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(require_super_admin),
):
    with rls_session(user_id=auth.user_id) as s:
        rows = s.execute(
            text(
                "SELECT a.id, a.action, a.target_type, a.target_id, a.result, a.created_at,"
                " u.display_name AS actor"
                " FROM audit_log a LEFT JOIN app_user u ON u.id = a.actor_id"
                " ORDER BY a.id DESC LIMIT :l OFFSET :o"
            ),
            {"l": min(limit, 200), "o": offset},
        ).mappings().all()
        total = s.execute(text("SELECT count(*) FROM audit_log")).scalar_one()
    return {"total": total, "items": [dict(r) for r in rows]}


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class OriginalGrantIn(BaseModel):
    application_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    minutes: int = Field(default=30, ge=1, le=120)
    reason: str = Field(min_length=1, max_length=250)


class SchoolFilterIn(BaseModel):
    job_id: int = Field(gt=0)
    tag_code: str = Field(min_length=1, max_length=80)
    enabled: bool
    reason: str = Field(min_length=1, max_length=250)


class PolicyIn(BaseModel):
    field_key: str = Field(min_length=1, max_length=120)
    purpose: Literal["preview", "filter", "sort", "search", "export"]
    allowed: bool
    reason: str = Field(min_length=1, max_length=250)


class IntegrationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    job_ids: list[int] = Field(max_length=100)
    allowed_fields: list[str] = Field(max_length=100)
    status: Literal["active", "disabled"] = "active"


@router.get("/settings")
def settings(auth: AuthContext = Depends(require_super_admin)):
    with rls_session(user_id=auth.user_id) as s:
        queries = {
            "departments": "SELECT id,name,status FROM department ORDER BY id",
            "users": """SELECT u.id,u.display_name,u.email,u.status,exists(SELECT 1 FROM system_admin x WHERE x.user_id=u.id) AS super_admin,
                         ARRAY(SELECT d.department_id FROM department_admin d WHERE d.user_id=u.id ORDER BY d.department_id) AS departments
                         FROM app_user u ORDER BY u.id""",
            "fields": """SELECT f.field_key,f.display_name,ARRAY(SELECT p.purpose FROM field_usage_policy p
                         JOIN search_state st USING(policy_version) WHERE p.field_key=f.field_key AND p.allowed ORDER BY p.purpose) AS purposes
                         FROM field_definition f WHERE f.status='active' ORDER BY f.field_key""",
            "integrations": """SELECT c.id,c.name,c.status,c.allowed_fields,
                         ARRAY(SELECT j.job_id FROM integration_job j WHERE j.client_id=c.id ORDER BY j.job_id) AS job_ids
                         FROM integration_client c ORDER BY c.id""",
            "school_tags": "SELECT DISTINCT tag_code FROM school_tag WHERE scope='school' ORDER BY tag_code",
            "school_filters": "SELECT job_id,tag_code,reason FROM job_school_filter ORDER BY job_id,tag_code",
        }
        return {key: [dict(row) for row in s.execute(text(query)).mappings()] for key, query in queries.items()}


@router.post("/departments", status_code=201)
def create_department(body: DepartmentIn, auth: AuthContext = Depends(require_super_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请输入部门名称")
    with rls_session(user_id=auth.user_id) as s:
        key = s.execute(text("INSERT INTO department(name) VALUES(:n) ON CONFLICT DO NOTHING RETURNING id"),
                        {"n": name}).scalar_one_or_none()
        if key is None:
            raise HTTPException(409, "部门已存在")
        audit(s, auth.user_id, "department_create", key, target_type="department")
    return {"id": key}


@router.post("/original-grants")
def grant_original(body: OriginalGrantIn, auth: AuthContext = Depends(require_super_admin)):
    if body.user_id == auth.user_id or not body.reason.strip():
        raise HTTPException(422, "须由另一名管理员授权并填写理由")
    with owner_engine.begin() as cn:
        if not cn.execute(text("""
            SELECT 1 FROM application a JOIN app_user u ON u.id=:u AND u.status='active'
            WHERE a.id=:a AND a.deleted_at IS NULL AND (
                EXISTS (SELECT 1 FROM department_admin d WHERE d.user_id=u.id AND d.department_id=a.handling_department_id)
                OR EXISTS (SELECT 1 FROM system_admin s WHERE s.user_id=u.id))
        """), {"u": body.user_id, "a": body.application_id}).first():
            raise HTTPException(404, "申请不存在或该账号无业务权限")
        cn.execute(text("""
            INSERT INTO resume_original_grant(application_id,user_id,granted_by,expires_at,reason)
            VALUES(:a,:u,:actor,now()+make_interval(mins=>:minutes),:reason)
            ON CONFLICT(application_id,user_id) DO UPDATE
            SET granted_by=excluded.granted_by,expires_at=excluded.expires_at,reason=excluded.reason
        """), {"a": body.application_id, "u": body.user_id, "actor": auth.user_id,
               "minutes": body.minutes, "reason": masked(body.reason)})
        audit(cn, auth.user_id, "original_grant", body.application_id, {"user_id": body.user_id, "reason": masked(body.reason)})
    return {"ok": True}


@router.post("/school-filters")
def school_filter(body: SchoolFilterIn, auth: AuthContext = Depends(require_super_admin)):
    if not body.reason.strip():
        raise HTTPException(422, "请填写岗位审批理由")
    with owner_engine.begin() as cn:
        if not cn.execute(text("""
            SELECT 1 FROM job WHERE id=:j AND EXISTS (SELECT 1 FROM school_tag WHERE tag_code=:t AND scope='school')
        """), {"j": body.job_id, "t": body.tag_code}).first():
            raise HTTPException(404, "岗位或院校标签不存在")
        if body.enabled:
            cn.execute(text("""
                INSERT INTO job_school_filter(job_id,tag_code,approved_by,reason) VALUES(:j,:t,:u,:r)
                ON CONFLICT (job_id,tag_code) DO UPDATE SET approved_by=excluded.approved_by,reason=excluded.reason
            """), {"j": body.job_id, "t": body.tag_code, "u": auth.user_id, "r": masked(body.reason)})
        else:
            cn.execute(text("DELETE FROM job_school_filter WHERE job_id=:j AND tag_code=:t"), {"j": body.job_id, "t": body.tag_code})
        bump_search(cn)
        audit(cn, auth.user_id, "school_filter_policy", body.job_id, {"tag": body.tag_code, "allowed": body.enabled, "reason": masked(body.reason)}, "job")
    return {"ok": True}


CARD_PREVIEW_ONLY = {"contact.phone", "profile.gender", "profile.address", "profile.avatar"}


@router.post("/field-policies")
def field_policy(body: PolicyIn, auth: AuthContext = Depends(require_super_admin)):
    if not body.reason.strip() or body.allowed and (
        (PRIVATE_FIELD.search(body.field_key) or body.field_key in CARD_PREVIEW_ONLY)
        and not (body.field_key in CARD_PREVIEW_ONLY | {"profile.photo_present"} and body.purpose == "preview")
    ):
        raise HTTPException(422, "此用途不允许或缺少授权理由")
    with owner_engine.begin() as cn:
        if not cn.execute(text("SELECT 1 FROM field_definition WHERE field_key=:f AND status='active'"), {"f": body.field_key}).first():
            raise HTTPException(404, "字段未登记")
        current = cn.execute(text("SELECT policy_version FROM search_state FOR UPDATE")).scalar_one()
        new = current + 1
        cn.execute(text("""
            INSERT INTO field_usage_policy(field_key,purpose,allowed,reason,policy_version,granted_by)
            SELECT field_key,purpose,allowed,reason,:new,granted_by FROM field_usage_policy WHERE policy_version=:old
            AND (field_key,purpose)<>(:f,:p)
        """), {"new": new, "old": current, "f": body.field_key, "p": body.purpose})
        insert(cn, "field_usage_policy", [{"field_key": body.field_key, "purpose": body.purpose, "allowed": body.allowed,
              "reason": masked(body.reason), "policy_version": new, "granted_by": auth.user_id}])
        versions = cn.execute(text("""
            SELECT DISTINCT active_version_id FROM application
            WHERE deleted_at IS NULL AND active_version_id IS NOT NULL
        """)).scalars().all()
        for version in versions:
            build_projection(cn, version, new)
        cn.execute(text("UPDATE search_state SET policy_version=:v,search_revision=search_revision+1"), {"v": new})
        audit(cn, auth.user_id, "field_usage_policy_change", body.field_key,
              {"purpose": body.purpose, "allowed": body.allowed, "reason": masked(body.reason)}, "field")
    return {"policy_version": new}


@router.post("/integrations", status_code=201)
@router.put("/integrations/{client_id}")
def write_integration(body: IntegrationIn, client_id: int | None = None, auth: AuthContext = Depends(require_super_admin)):
    if not body.name.strip():
        raise HTTPException(422, "请输入集成账号名称")
    if any(PRIVATE_FIELD.search(field) or field in CARD_PREVIEW_ONLY for field in body.allowed_fields):
        raise HTTPException(422, "集成账号字段包含受限内容")
    params = {"id": client_id, "name": body.name.strip(), "status": body.status, "fields": list(dict.fromkeys(body.allowed_fields)),
              "jobs": list(set(body.job_ids))}
    with owner_engine.begin() as cn:
        found = cn.execute(text("SELECT count(*) FROM job WHERE id=ANY(CAST(:jobs AS bigint[]))"), params).scalar_one()
        count = cn.execute(text("SELECT count(*) FROM field_definition WHERE field_key=ANY(CAST(:fields AS text[])) AND status='active'"), params).scalar_one()
        if found != len(params["jobs"]) or count != len(params["fields"]):
            raise HTTPException(422, "岗位或字段白名单无效")
        if client_id is None:
            client_id = cn.execute(text("""
                INSERT INTO integration_client(name,status,allowed_fields) VALUES(:name,:status,CAST(:fields AS text[]))
                ON CONFLICT DO NOTHING RETURNING id
            """), params).scalar_one_or_none()
            if client_id is None:
                raise HTTPException(409, "名称已存在")
        elif not cn.execute(text("""
            UPDATE integration_client SET name=:name,status=:status,allowed_fields=CAST(:fields AS text[]),
                perm_version=perm_version+1 WHERE id=:id RETURNING id
        """), params).first():
            raise HTTPException(404, "集成账号不存在")
        cn.execute(text("DELETE FROM integration_job WHERE client_id=:client"), {"client": client_id})
        cn.execute(text("""
            INSERT INTO integration_job(client_id,job_id,handling_department_id)
            SELECT :client,id,department_id FROM job WHERE id=ANY(CAST(:jobs AS bigint[]))
        """), {"client": client_id, "jobs": params["jobs"]})
        audit(cn, auth.user_id, "integration_config", client_id, target_type="integration_client")
    return {"id": client_id}


@router.post("/cleanup-files")
def retry_cleanup(auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.begin() as cn:
        remaining = cleanup_files(cn)
        audit(cn, auth.user_id, "file_cleanup", auth.user_id, {"remaining": remaining}, "storage")
    return {"remaining": remaining}


class FieldIn(BaseModel):
    field_key: str = Field(pattern=r"^x_[.][a-z0-9_]{2,60}$")
    display_name: str = Field(min_length=1, max_length=80)
    alias: str = Field(min_length=1, max_length=80)
    value_type: Literal["text", "number", "date", "period", "boolean", "range", "location", "organization", "unknown"]


@router.post("/fields", status_code=201)
def register_field(body: FieldIn, auth: AuthContext = Depends(require_super_admin)):
    name, alias = body.display_name.strip(), norm(body.alias)
    if not name or not alias or PRIVATE_FIELD.search(" ".join((body.field_key, name, alias))):
        raise HTTPException(422, "字段名称受限或无效")
    with owner_engine.begin() as cn:
        key = cn.execute(text("""
            INSERT INTO field_definition(field_key,display_name,value_type) VALUES(:key,:name,:kind)
            ON CONFLICT DO NOTHING RETURNING field_key
        """), {"key": body.field_key, "name": name, "kind": body.value_type}).scalar_one_or_none()
        if key is None or not cn.execute(text("""
            INSERT INTO field_alias(alias_normalized,field_key) VALUES(:alias,:key)
            ON CONFLICT DO NOTHING RETURNING field_key
        """), {"alias": alias, "key": key}).first():
            raise HTTPException(409, "字段编号或别名已登记")
        audit(cn, auth.user_id, "field_register", key, target_type="field")
    return {"field_key": key}
