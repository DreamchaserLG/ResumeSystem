"""超管管理接口：账号、部门 HR 授权/撤权、审计查询。

授权变更（grant/revoke）必须 bump_perm_version（02 §3）并写审计。
"""
import base64
import binascii
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.auth import AuthContext, require_super_admin
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked, norm
from app.core.integration import PRIVATE_FIELD
from app.core.resume_pipeline import build_projection, bump_search, insert
from app.core.workflow import audit, cleanup_files
from app.core.security import hash_password
from app.api.profile import MAX_UPLOAD, PRIVATE_HEADERS, avatar_bytes

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserIn(BaseModel):
    display_name: str = Field(min_length=1,max_length=80)
    email: str = Field(min_length=3,max_length=160)
    password: str = Field(min_length=8,max_length=128)
    department_id: int | None = Field(default=None, gt=0)
    phone: str = Field(default="", max_length=32)
    office: str = Field(default="", max_length=80)
    avatar_base64: str | None = None


class GrantIn(BaseModel):
    user_id: int
    department_id: int


class UserStatusIn(BaseModel):
    status: Literal["active", "disabled"]


@router.post("/users")
def create_user(body: CreateUserIn, auth: AuthContext = Depends(require_super_admin)):
    name, email = body.display_name.strip(), body.email.strip().lower()
    if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "请填写姓名与有效的邮箱")
    phone, office = body.phone.strip(), body.office.strip()
    if phone and not re.fullmatch(r"[0-9+ ()\-]{3,32}", phone):
        raise HTTPException(422, "工作电话只能包含数字、空格和 + ( ) -。")
    image = None
    if body.avatar_base64:
        if len(body.avatar_base64) > (MAX_UPLOAD * 4 // 3 + 8):
            raise HTTPException(413, "头像不能超过 2 MB。")
        try:
            raw = base64.b64decode(body.avatar_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, "头像数据无效。") from exc
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "头像不能超过 2 MB。")
        image = avatar_bytes(raw)
    with owner_engine.begin() as s:
        if body.department_id is not None and not s.execute(text(
            "SELECT 1 FROM department WHERE id=:d AND status='active' FOR SHARE"
        ), {"d": body.department_id}).first():
            raise HTTPException(404, "部门不存在或已停用")
        new_id = s.execute(text("""
            INSERT INTO app_user(display_name,email,password_hash) VALUES(:name,:email,:password)
            ON CONFLICT(email) DO NOTHING RETURNING id
        """), {"name": name, "email": email, "password": hash_password(body.password)}).scalar_one_or_none()
        if new_id is None:
            raise HTTPException(409, "邮箱已存在")
        s.execute(text("""
            INSERT INTO user_profile(user_id,phone,office,avatar_webp)
            VALUES(:id,:phone,:office,:avatar)
        """), {"id":new_id,"phone":phone,"office":office,"avatar":image})
        audit(s, auth.user_id, "user_create", new_id, target_type="user")
        if body.department_id is not None:
            s.execute(text("""
                INSERT INTO department_admin(department_id,user_id,granted_by) VALUES(:d,:u,:actor)
            """), {"d": body.department_id, "u": new_id, "actor": auth.user_id})
            audit(s, auth.user_id, "permission_grant", new_id, {"department_id": body.department_id}, "user")
    return {"user_id": new_id, "department_id": body.department_id}


@router.get("/users/{user_id}/avatar")
def managed_user_avatar(user_id: int, auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.connect() as s:
        data = s.execute(text("""
            SELECT p.avatar_webp FROM user_profile p JOIN app_user u ON u.id=p.user_id
            WHERE u.id=:id AND u.status<>'deleted'
        """), {"id":user_id}).scalar_one_or_none()
    if data is None:
        raise HTTPException(404, "尚未设置头像")
    return Response(data, media_type="image/webp", headers=PRIVATE_HEADERS)


def _lock_managed_account(connection, auth, user_id):
    # Serialize demotions/deletions so the last active super administrator cannot be removed
    # by two concurrent operations. Check the actor again after taking the lock.
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('resume:account-lifecycle'))"))
    actor = connection.execute(text("""
        SELECT u.perm_version FROM app_user u JOIN system_admin sa ON sa.user_id=u.id
        WHERE u.id=:id AND u.status='active' FOR SHARE OF u
    """), {"id": auth.user_id}).scalar_one_or_none()
    if actor != auth.perm_version:
        raise HTTPException(401, "账号权限已变更，请重新登录")
    if user_id == auth.user_id:
        raise HTTPException(409, "不能停用、启用或删除正在使用的账号")
    row = connection.execute(text("""
        SELECT u.status, EXISTS(SELECT 1 FROM system_admin sa WHERE sa.user_id=u.id) AS is_admin
        FROM app_user u WHERE u.id=:id AND u.status<>'deleted' FOR UPDATE OF u
    """), {"id": user_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "账号不存在或已删除")
    if row["is_admin"] and row["status"] == "active" and not connection.execute(text("""
        SELECT 1 FROM system_admin sa JOIN app_user u ON u.id=sa.user_id
        WHERE sa.user_id<>:id AND u.status='active' LIMIT 1
    """), {"id": user_id}).first():
        raise HTTPException(409, "必须保留至少一个启用的超级管理员")
    return row


@router.put("/users/{user_id}/status")
def set_user_status(user_id: int, body: UserStatusIn, auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.begin() as cn:
        row = _lock_managed_account(cn, auth, user_id)
        if row["status"] != body.status:
            cn.execute(text("UPDATE app_user SET status=:status,perm_version=perm_version+1 WHERE id=:id"),
                       {"id":user_id,"status":body.status})
            audit(cn, auth.user_id, "user_status_change", user_id,
                  {"from":row["status"],"to":body.status}, "user")
    return {"id":user_id,"status":body.status}


@router.delete("/users/{user_id}")
def remove_user(user_id: int, auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.begin() as cn:
        _lock_managed_account(cn, auth, user_id)
        cn.execute(text("DELETE FROM department_admin WHERE user_id=:id"), {"id": user_id})
        cn.execute(text("DELETE FROM system_admin WHERE user_id=:id"), {"id": user_id})
        cn.execute(text("DELETE FROM resume_original_grant WHERE user_id=:id"), {"id": user_id})
        cn.execute(text("DELETE FROM user_profile WHERE user_id=:id"), {"id": user_id})
        cn.execute(text("""
            UPDATE app_user SET status='deleted',email=:tombstone,password_hash='',perm_version=perm_version+1
            WHERE id=:id
        """), {"id":user_id,"tombstone":f"deleted-{uuid4().hex}@deleted.invalid"})
        audit(cn, auth.user_id, "user_delete", user_id, target_type="user")
    return {"ok":True}


@router.get("/departments/{department_id}/accounts")
def department_accounts(department_id: int, auth: AuthContext = Depends(require_super_admin)):
    with rls_session(user_id=auth.user_id) as s:
        if not s.execute(text("SELECT 1 FROM department WHERE id=:id"), {"id": department_id}).first():
            raise HTTPException(404, "部门不存在")
        rows = s.execute(text("""
            SELECT u.id,u.display_name,u.email,u.status
            FROM department_admin da JOIN app_user u ON u.id=da.user_id
            WHERE da.department_id=:id AND u.status<>'deleted' ORDER BY u.display_name,u.id
        """), {"id": department_id}).mappings()
        return {"items": [dict(row) for row in rows]}


@router.post("/grant-dept-hr")
def grant_dept_hr(body: GrantIn, auth: AuthContext = Depends(require_super_admin)):
    with rls_session(user_id=auth.user_id) as s:
        dept = s.execute(
            text("SELECT name FROM department WHERE id = :d"), {"d": body.department_id}
        ).fetchone()
        if dept is None:
            raise HTTPException(status_code=404, detail="部门不存在")
        user = s.execute(
            text("SELECT email FROM app_user WHERE id = :u AND status<>'deleted' FOR UPDATE"), {"u": body.user_id}
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
                         FROM app_user u WHERE u.status<>'deleted' ORDER BY u.id""",
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


@router.get("/organization")
def organization(auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.connect() as s:
        queries = {
            "companies": "SELECT id,name,parent_id FROM recruiting_company ORDER BY parent_id NULLS FIRST,name,id",
            "departments": """
                SELECT d.id,d.name,d.status,d.company_id,c.name AS company_name
                FROM department d JOIN recruiting_company c ON c.id=d.company_id ORDER BY c.name,d.name,d.id
            """,
            "users": """
                SELECT u.id,u.display_name,u.email,u.status,(sa.user_id IS NOT NULL) AS super_admin,
                    EXISTS(SELECT 1 FROM user_profile p WHERE p.user_id=u.id AND p.avatar_webp IS NOT NULL) AS has_avatar,
                    ARRAY(SELECT d.department_id FROM department_admin d WHERE d.user_id=u.id ORDER BY d.department_id) AS departments
                FROM app_user u LEFT JOIN system_admin sa ON sa.user_id=u.id
                WHERE u.status<>'deleted'
                ORDER BY (sa.user_id IS NOT NULL) DESC,u.display_name,u.id
            """,
        }
        return {key:[dict(row) for row in s.execute(text(query)).mappings()] for key,query in queries.items()}


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
