"""Single job file upload; parsing never writes a job or binds a department."""
import base64
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from redis.exceptions import RedisError

from app.api.uploads import validate_upload
from app.core.auth import AuthContext, require_recruitment_access
from app.core.cache import get_redis
from worker.tasks.job_import import parse_job_file

router = APIRouter(prefix="/workspace/job-imports", tags=["job import"])
MIME = {".pdf": "application/pdf", ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".txt": "text/plain"}


@router.post("", status_code=202)
def upload_job(file: list[UploadFile] = File(...), auth: AuthContext = Depends(require_recruitment_access)):
    if len(file) != 1:
        raise HTTPException(422, "每次只能导入一份岗位文件")
    file = file[0]
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MIME:
        raise HTTPException(415, "仅支持 PDF、Word、图片、TXT")
    data = file.file.read(5 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(422, "文件为空")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "岗位文件不能超过 5 MB")
    if ext != ".txt":
        try:
            validate_upload(MIME[ext], data)
        except HTTPException as exc:
            raise HTTPException(415, "文件内容与岗位文件格式不符") from exc
    job_import_id = str(uuid.uuid4())
    key = f"rs:job-import:{job_import_id}"
    try:
        cache = get_redis()
        cache.setex(key, 600, json.dumps({
            "user_id": auth.user_id, "status": "pending", "extension": ext, "mime": MIME[ext],
            "data": base64.b64encode(data).decode("ascii"),
        }))
        parse_job_file.delay(job_import_id)
    except Exception as exc:
        try:
            get_redis().delete(key)
        except RedisError:
            pass
        raise HTTPException(503, "岗位解析队列暂不可用，请重试或手动新建") from exc
    return {"id": job_import_id, "status": "pending"}


@router.get("/{job_import_id}")
def job_import_result(job_import_id: uuid.UUID, auth: AuthContext = Depends(require_recruitment_access)):
    try:
        raw = get_redis().get(f"rs:job-import:{job_import_id}")
    except RedisError as exc:
        raise HTTPException(503, "暂时无法读取解析结果，请重试") from exc
    state = json.loads(raw) if raw else {}
    if state.get("user_id") != auth.user_id:
        raise HTTPException(404, "岗位解析任务不存在或已过期")
    return {key: state[key] for key in ("status", "draft", "error") if key in state}
