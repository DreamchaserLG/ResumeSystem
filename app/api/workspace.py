"""Follow-up records and the company hierarchy for the recruiting workspace."""
from datetime import datetime
from pathlib import PurePath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.auth import AuthContext, require_recruitment_access, require_super_admin
from app.core.db import owner_engine, rls_session
from app.core.extraction import masked
from app.core.workflow import application_lock, audit, fresh_departments

router = APIRouter(tags=["recruiting workspace"])

NOTE_FIELDS = "id,author_name,body,action,scheduled_at,attachment_name,created_at"


def accessible_application(session, application_id, auth):
    if session.execute(text("""
        SELECT 1 FROM application WHERE id=:id AND deleted_at IS NULL
            AND handling_department_id=ANY(CAST(:deps AS bigint[]))
    """), {"id": application_id, "deps": auth.departments}).first() is None:
        raise HTTPException(404, "申请不存在")


@router.get("/workspace/followup-options")
def followup_options(auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments,user_id=auth.user_id) as session:
        options = session.execute(text("""
            SELECT recent.body FROM application a
            JOIN LATERAL (
                SELECT body,id FROM application_followup
                WHERE application_id=a.id ORDER BY id DESC LIMIT 1
            ) recent ON true
            WHERE a.deleted_at IS NULL AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
            GROUP BY recent.body ORDER BY max(recent.id) DESC LIMIT 30
        """), {"deps":auth.departments}).scalars().all()
    return {"items":options}


@router.get("/workspace/applications/{application_id}/collaborators")
def collaborators(application_id: int, auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments,user_id=auth.user_id) as session:
        accessible_application(session,application_id,auth)
        rows = session.execute(text("""
            SELECT u.id,u.display_name FROM application a
            JOIN department_admin d ON d.department_id=a.handling_department_id
            JOIN app_user u ON u.id=d.user_id AND u.status='active'
            WHERE a.id=:id AND a.deleted_at IS NULL AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))
            ORDER BY u.display_name,u.id LIMIT 100
        """), {"id":application_id,"deps":auth.departments}).mappings().all()
    return {"items":[dict(row) for row in rows]}


@router.get("/workspace/applications/{application_id}/followups")
def followups(application_id: int, before: int | None = None, auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        accessible_application(session, application_id, auth)
        rows = session.execute(text(f"""
            SELECT {NOTE_FIELDS} FROM application_followup
            WHERE application_id=:id AND (CAST(:before AS bigint) IS NULL OR id<:before)
            ORDER BY id DESC LIMIT 51
        """), {"id":application_id,"before":before}).mappings().all()
    return {"items":[dict(row) for row in rows[:50]],"next":rows[49]["id"] if len(rows)>50 else None}


@router.post("/workspace/applications/{application_id}/followups", status_code=201)
async def add_followup(
    application_id: int,
    content: str = Form(min_length=1,max_length=1000),
    action: Literal["note","interview"] = Form(default="note"),
    scheduled_at: datetime | None = Form(default=None),
    attachment: UploadFile | None = File(default=None),
    auth: AuthContext = Depends(require_recruitment_access),
):
    body = masked(content.strip())
    if not body or (action=="interview") != bool(scheduled_at) or (scheduled_at and scheduled_at.utcoffset() is None):
        raise HTTPException(422,"请填写跟进内容。面试记录需要明确的时间和时区")
    filename, data = None, None
    if attachment is not None:
        filename = PurePath((attachment.filename or "").replace(chr(92),"/")).name[:180]
        if PurePath(filename).suffix.lower() not in {".pdf",".doc",".docx",".png",".jpg",".jpeg",".webp",".txt"}:
            raise HTTPException(415,"跟进附件支持 PDF、Word、图片和文本")
        data = await attachment.read(5*1024*1024+1)
        if not data or len(data)>5*1024*1024:
            raise HTTPException(422,"跟进附件须大于 0 且不超过 5 MB")
    with owner_engine.begin() as connection:
        application_lock(connection, application_id, auth)
        row = connection.execute(text(f"""
            INSERT INTO application_followup(application_id,author_id,author_name,body,action,scheduled_at,attachment_name,attachment_data)
            VALUES(:id,:author,:name,:body,:action,:when,:filename,:data) RETURNING {NOTE_FIELDS}
        """), {"id":application_id,"author":auth.user_id,"name":auth.display_name,"body":body,
              "action":action,"when":scheduled_at,"filename":filename,"data":data}).mappings().one()
        audit(connection, auth.user_id, "recruiting_followup", application_id, {"followup_id":row["id"],"action":action})
    return dict(row)


@router.get("/workspace/applications/{application_id}/followups/{note_id}/attachment")
def followup_attachment(application_id: int, note_id: int, auth: AuthContext = Depends(require_recruitment_access)):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as session:
        accessible_application(session, application_id, auth)
        row = session.execute(text("""
            SELECT attachment_name,attachment_data FROM application_followup
            WHERE application_id=:a AND id=:id AND attachment_data IS NOT NULL
        """), {"a":application_id,"id":note_id}).one_or_none()
    if row is None:
        raise HTTPException(404,"附件不存在")
    return Response(bytes(row.attachment_data),media_type="application/octet-stream",headers={
        "Content-Disposition":"attachment; filename*=UTF-8''"+quote(row.attachment_name,safe=""),
        "Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff",
    })


class CompanyIn(BaseModel):
    name: str = Field(min_length=1,max_length=80)

class Placement(BaseModel):
    company_id: int = Field(gt=0)


@router.post("/workspace/companies", status_code=201)
def create_company(body: CompanyIn, auth: AuthContext = Depends(require_super_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422,"请输入子公司名称")
    with owner_engine.begin() as connection:
        fresh_departments(connection, auth)
        company_id = connection.execute(text("""
            INSERT INTO recruiting_company(name,parent_id) VALUES(:name,1)
            ON CONFLICT (name) DO NOTHING RETURNING id
        """), {"name":name}).scalar_one_or_none()
        if company_id is None:
            raise HTTPException(409,"公司名称已存在")
        audit(connection,auth.user_id,"company_create",company_id,target_type="company")
    return {"id":company_id}


@router.put("/workspace/departments/{department_id}/company")
def assign_company(department_id: int, body: Placement, auth: AuthContext = Depends(require_super_admin)):
    with owner_engine.begin() as connection:
        fresh_departments(connection, auth)
        if not connection.execute(text("""
            UPDATE department SET company_id=:company WHERE id=:department AND status='active'
              AND EXISTS (SELECT 1 FROM recruiting_company WHERE id=:company) RETURNING id
        """), {"company":body.company_id,"department":department_id}).first():
            raise HTTPException(404,"公司或部门不存在")
        audit(connection,auth.user_id,"department_company",department_id,{"company_id":body.company_id},target_type="department")
    return {"ok":True}
