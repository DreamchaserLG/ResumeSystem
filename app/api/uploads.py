"""简历导入接口（03 流水线入口）。

POST /applications：部门 HR 导入简历（multipart）→ 建档（candidate/document/version/application）
→ 异步解析（worker.process_resume）→ 返回跟踪 ID。
GET /applications/{id}/resumes/{version_id}：解析状态 + spans 摘要（RLS 边界内）。
"""
import hashlib
import io
import uuid
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import text

from app.core.auth import AuthContext, require_dept_hr
from app.core.db import rls_session
from app.core.storage import save_original
from app.core.workflow import audit, fresh_departments, schedule_parse

router = APIRouter(tags=["import"])

ALLOWED_MIME = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/png": "img",
    "image/jpeg": "img",
    "image/webp": "img",
}


def validate_upload(mime, data):
    if mime == "application/pdf":
        valid = data.startswith(b"%PDF-")
    elif mime == "application/msword":
        valid = data.startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
    elif mime.endswith("wordprocessingml.document"):
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                valid = "word/document.xml" in archive.namelist() and sum(item.file_size for item in entries) <= 80 * 1024 * 1024
        except (BadZipFile, OSError):
            valid = False
    elif mime == "image/png":
        valid = data.startswith(bytes.fromhex("89504e470d0a1a0a"))
    elif mime == "image/jpeg":
        valid = data.startswith(bytes.fromhex("ffd8ff"))
    elif mime == "image/webp":
        valid = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    else:
        valid = False
    if not valid:
        raise HTTPException(415, "文件内容与简历格式不符")


@router.post("/applications", status_code=202)
async def import_resume(
    job_id: int,
    file: UploadFile,
    candidate_application_id: int | None = None,
    auth: AuthContext = Depends(require_dept_hr),
):
    # ① 岗位必须属于授权部门
    with rls_session(departments=auth.departments, user_id=auth.user_id) as s:
        job = s.execute(
            text("SELECT id, department_id FROM job WHERE id = :i AND status='open'"),
            {"i": job_id},
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="岗位不存在或已关闭")

    # ② 文件类型校验
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    mime = (file.content_type or "").lower().split(";")[0]
    if mime not in ALLOWED_MIME and f".{ext}" not in (".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=415, detail="仅支持 PDF / DOCX / 图片简历")
    data = await file.read(20 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(422, "文件为空")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 20MB 上限")

    mime = {
        "pdf": "application/pdf", "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    }.get(ext, mime)
    validate_upload(mime, data)
    # ③ 建档（owner 事务：跨表一致性；RLS 写权限由服务层封装）
    candidate_key = uuid.uuid4().hex[:12]

    from app.core.db import owner_engine

    with owner_engine.begin() as cn:
        departments = fresh_departments(cn, auth)
        job = cn.execute(
            text("SELECT id,department_id FROM job WHERE id=:id AND status='open' AND department_id=ANY(CAST(:deps AS bigint[])) FOR SHARE"),
            {"id": job_id, "deps": departments},
        ).fetchone()
        if not job:
            raise HTTPException(404, "岗位不存在或已关闭")
        if candidate_application_id is None:
            cand = cn.execute(text("INSERT INTO candidate DEFAULT VALUES RETURNING id")).scalar_one()
        else:
            cand = cn.execute(text("""
                SELECT candidate_id FROM application WHERE id=:a AND deleted_at IS NULL
                    AND handling_department_id=ANY(CAST(:deps AS bigint[])) FOR SHARE
            """), {"a": candidate_application_id, "deps": departments}).scalar_one_or_none()
            if cand is None:
                raise HTTPException(404, "关联申请不存在")
            cn.execute(text("SELECT id FROM candidate WHERE id=:c FOR UPDATE"), {"c": cand})
        sha = hashlib.sha256(data).hexdigest()
        doc_id = cn.execute(text("""
            SELECT id FROM resume_document WHERE candidate_id=:c AND sha256=:hash FOR UPDATE
        """), {"c": cand, "hash": sha}).scalar_one_or_none()
        if doc_id is None:
            object_key, _ = save_original(candidate_key, file.filename or "resume", data)
            doc_id = cn.execute(text("""
                INSERT INTO resume_document(candidate_id,object_key,mime_type,sha256,file_size)
                VALUES(:c,:k,:m,:hash,:fs) RETURNING id
            """), {"c": cand, "k": object_key, "m": mime, "hash": sha, "fs": len(data)}).scalar_one()
        ver = cn.execute(text("""
            INSERT INTO resume_version(document_id,version_no,parser_version)
            SELECT :d,coalesce(max(version_no),0)+1,'pending'
            FROM resume_version WHERE document_id=:d RETURNING id
        """), {"d": doc_id}).scalar_one()
        app_id = cn.execute(
            text(
                "INSERT INTO application (job_id, candidate_id, submitted_department_id,"
                " handling_department_id, submitted_version_id, status)"
                " VALUES (:j, :c, :dep, :dep, :v, 'pending') RETURNING id"
            ),
            {"j": job_id, "c": cand, "dep": job.department_id, "v": ver},
        ).scalar_one()

        audit(cn, auth.user_id, "resume_import", app_id, {"version_id": ver})

    # ④ 异步解析
    schedule_parse(ver)

    return {
        "application_id": app_id,
        "version_id": ver,
        "candidate_id": cand,
        "status": "parsing",
    }


@router.get("/applications/{application_id}/resumes/{version_id}")
def resume_status(
    application_id: int,
    version_id: int,
    auth: AuthContext = Depends(require_dept_hr),
):
    with rls_session(departments=auth.departments, user_id=auth.user_id) as s:
        row = s.execute(
            text(
                "SELECT v.id, v.status, v.fail_reason, v.parser_version, v.published_at,"
                " d.has_photo, d.preview_key"
                " FROM resume_version v"
                " JOIN resume_document d ON d.id = v.document_id"
                " JOIN application a ON a.active_version_id = v.id"
                "    OR a.submitted_version_id = v.id"
                " WHERE v.id = :vid AND a.id = :aid AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))"
            ),
            {"vid": version_id, "aid": application_id, "deps": auth.departments},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="申请或版本不存在")
        spans = s.execute(
            text(
                "SELECT span_no, span_kind, extraction_method, ocr_confidence"
                " FROM resume_source_span WHERE version_id = :v ORDER BY span_no"
            ),
            {"v": version_id},
        ).mappings().all()
    return {
        "version_id": row.id,
        "status": row.status,
        "fail_reason": row.fail_reason,
        "parser_version": row.parser_version,
        "published_at": row.published_at,
        "has_photo": row.has_photo,
        "spans": [dict(r) for r in spans],
    }
