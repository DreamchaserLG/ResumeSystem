"""岗位文件夹、同步任务及可见目录。"""
from ftplib import Error
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import ftp_catalog, ftp_source
from app.core.auth import AuthContext, require_auth, require_recruitment_access
from app.core.cache import get_redis
from app.core.db import owner_engine
from app.core.workflow import fresh_departments
from worker.tasks.ftp_catalog import sync_ftp_catalog

router = APIRouter(prefix="/job-catalog", tags=["岗位文件夹"])


class FolderInput(BaseModel):
    parent: str = Field(default="", max_length=1000)
    name: str = Field(min_length=1, max_length=255)
    department_id: int | None = Field(default=None, gt=0)


def _require_config():
    if not ftp_source.enabled():
        raise HTTPException(503, "FTP 服务尚未配置")


def _admin(auth):
    if auth.role != "super_admin":
        raise HTTPException(403, "需要超级管理员权限")
    with owner_engine.begin() as connection:
        fresh_departments(connection, auth)


@router.get("")
def list_folders(auth: AuthContext = Depends(require_auth)):
    configured = ftp_source.enabled()
    admin = auth.role == "super_admin"
    if not configured:
        return {"enabled": False, "synced_at": None, "folders": []}
    with owner_engine.begin() as connection:
        scope = set(fresh_departments(connection, auth))
        timestamp = connection.execute(text("SELECT synced_at FROM ftp_catalog_state")).scalar_one_or_none()
        folders = {
            row["path"]: dict(row) for row in connection.execute(text("""
                SELECT f.path,f.department_id,f.job_id
                FROM ftp_catalog_folder f LEFT JOIN job j ON j.id=f.job_id
                WHERE f.present AND (j.id IS NULL OR NOT j.hidden) ORDER BY f.path
            """)).mappings()
        }
    visible = set(folders) if admin else set()
    if not admin:
        for path, folder in folders.items():
            if folder["department_id"] in scope:
                while path and path not in visible:
                    visible.add(path)
                    path = path.rpartition("/")[0]
    return {"enabled": True, "synced_at": timestamp, "folders": [
        {**folder, "writable": not folder["job_id"] and (admin or folder["department_id"] in scope)}
        for path, folder in folders.items() if path in visible
    ]}


@router.post("/folders", status_code=201)
def create_folder(body: FolderInput, auth: AuthContext = Depends(require_auth)):
    _require_config()
    try:
        return ftp_catalog.create_folder(body.parent, body.name.strip(), body.department_id, auth)
    except ftp_source.CatalogError as error:
        raise HTTPException(409, str(error)) from None
    except (Error, OSError, EOFError, ValueError):
        raise HTTPException(502, "FTP 写入失败，请检查连接与目录权限后重试") from None


@router.post("/sync", status_code=202)
def start_sync(auth: AuthContext = Depends(require_auth)):
    _require_config()
    _admin(auth)
    task_id = uuid4()
    try:
        sync_ftp_catalog.apply_async(args=[auth.user_id], task_id=f"ftp-sync-{task_id}", retry=False)
    except Exception:
        raise HTTPException(503, "同步队列暂不可用，请稍后重试") from None
    return {"id": task_id}


@router.get("/sync/{task_id}")
def sync_status(task_id: UUID, auth: AuthContext = Depends(require_auth)):
    _require_config()
    _admin(auth)
    run = sync_ftp_catalog.AsyncResult(f"ftp-sync-{task_id}")
    try:
        state = run.state
        if state == "SUCCESS":
            return run.result
        if state in {"FAILURE", "REVOKED"}:
            return {"status": "failed", "message": "同步任务未完成，请重试"}
    except Exception:
        raise HTTPException(503, "同步队列暂不可用，请稍后重试") from None
    return {"status": "running" if state == "STARTED" else "pending"}


@router.post("/jobs/{job_id}/uploads", status_code=201)
def upload_resume(job_id: int, request_id: UUID, file: UploadFile,
                  auth: AuthContext = Depends(require_recruitment_access)):
    _require_config()
    try:
        ftp_catalog.upload_resume(job_id, file.filename or "", request_id, file.file.read(ftp_source.MAX_FILE + 1), auth)
    except ftp_source.CatalogError as error:
        raise HTTPException(409, str(error)) from None
    except (Error, OSError, EOFError, ValueError):
        raise HTTPException(502, "FTP 上传失败，请稍后重试") from None
    try:
        if get_redis().set("rs:ftp-upload-sync", "1", ex=25, nx=True):
            sync_ftp_catalog.apply_async(countdown=20, retry=False)
    except Exception:
        # 文件已保存在 FTP，定时同步仍可以读取。
        pass
    return {"status": "stored"}
