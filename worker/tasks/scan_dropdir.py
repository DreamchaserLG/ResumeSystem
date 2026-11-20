"""Read an internal share mounted read-only as <department id>/<resume filename>."""
import hashlib
import logging
import os
import stat
import time
from pathlib import Path
from uuid import uuid4

from celery import shared_task
from fastapi import HTTPException
from sqlalchemy import text

from app.api.uploads import validate_upload
from app.core.config import get_settings
from app.core.db import owner_engine
from app.core.storage import save_original
from app.core.workflow import audit
from worker.tasks.parse import process_resume

logger = logging.getLogger(__name__)
MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
MAX_FILE = 20 * 1024 * 1024
SETTLE_SECONDS = 90


@shared_task(name="worker.scan_resume_dropdir", ignore_result=True)
def scan_resume_dropdir():
    configured = get_settings().resume_drop_dir
    if not configured:
        return {"queued": 0}
    root = Path(configured)
    with owner_engine.connect() as connection:
        departments = {str(department) for department in connection.execute(
            text("SELECT id FROM department WHERE status='active'")
        ).scalars()}
    queued = 0
    for directory in sorted(root.iterdir()):
        if directory.name not in departments or directory.is_symlink() or not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.name.startswith(".") or path.suffix.lower() not in MIME:
                continue
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or time.time()-info.st_mtime < SETTLE_SECONDS:
                    continue
            except FileNotFoundError:
                continue
            import_resume_dropfile.delay(int(directory.name), path.name)
            queued += 1
    logger.info("Resume share scan queued %s files", queued)
    return {"queued":queued}


def _read_file(directory: Path, filename: str):
    # Keep both path components confined to the share, even if a user replaces
    # a directory entry with a symlink between scan and import.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    folder = os.open(directory, flags | os.O_DIRECTORY)
    try:
        with os.fdopen(os.open(filename, flags, dir_fd=folder), "rb") as source:
            first = os.fstat(source.fileno())
            if not stat.S_ISREG(first.st_mode) or time.time()-first.st_mtime < SETTLE_SECONDS:
                return None
            data = source.read(MAX_FILE+1)
            last = os.fstat(source.fileno())
            if (first.st_size,first.st_mtime_ns) != (last.st_size,last.st_mtime_ns):
                return None
    finally:
        os.close(folder)
    return data


def _register(connection, params, data, filename, problem):
    if not connection.execute(text("""
        SELECT 1 FROM department WHERE id=:department AND status='active' FOR SHARE
    """), params).first():
        return None
    existing = connection.execute(text("""
        SELECT f.status,a.submitted_version_id FROM resume_dropfile f
        LEFT JOIN application a ON a.id=f.application_id AND a.deleted_at IS NULL
        WHERE f.department_id=:department AND f.sha256=:sha
    """), params).one_or_none()
    if existing:
        return existing.submitted_version_id if existing.status == "processing" else None
    if problem:
        connection.execute(text("""
            INSERT INTO resume_dropfile(object_key,sha256,mime_type,file_size,department_id,status,fail_reason,processed_at)
            VALUES(:path,:sha,:mime,:size,:department,'failed',:problem,now())
        """), {**params,"problem":problem})
        return None
    candidate = connection.execute(text("INSERT INTO candidate DEFAULT VALUES RETURNING id")).scalar_one()
    object_key, _ = save_original(uuid4().hex[:16], filename, data)
    document = connection.execute(text("""
        INSERT INTO resume_document(candidate_id,object_key,mime_type,sha256,file_size)
        VALUES(:candidate,:object,:mime,:sha,:size) RETURNING id
    """), {**params,"candidate":candidate,"object":object_key}).scalar_one()
    version = connection.execute(text("""
        INSERT INTO resume_version(document_id,version_no,parser_version)
        VALUES(:document,1,'pending') RETURNING id
    """), {"document":document}).scalar_one()
    application = connection.execute(text("""
        INSERT INTO application(candidate_id,submitted_department_id,handling_department_id,submitted_version_id)
        VALUES(:candidate,:department,:department,:version) RETURNING id
    """), {**params,"candidate":candidate,"version":version}).scalar_one()
    connection.execute(text("""
        INSERT INTO resume_dropfile(object_key,sha256,mime_type,file_size,department_id,candidate_id,application_id,status)
        VALUES(:path,:sha,:mime,:size,:department,:candidate,:application,'processing')
    """), {**params,"candidate":candidate,"application":application})
    audit(connection, None, "resume_share_import", application, {"version_id":version,"department_id":params["department"]})
    return version


@shared_task(name="worker.import_resume_dropfile", acks_late=True, ignore_result=True)
def import_resume_dropfile(department: int, filename: str):
    root = get_settings().resume_drop_dir
    mime = MIME.get(Path(filename).suffix.lower())
    if not root or not mime or not isinstance(department,int) or department <= 0 or Path(filename).name != filename:
        return
    try:
        data = _read_file(Path(root)/str(department), filename)
    except OSError:
        logger.warning("Unable to read a resume share file in department %s", department)
        return
    if data is None:
        return
    problem = None
    if len(data) > MAX_FILE:
        logger.warning("A resume share file exceeds 20 MB in department %s", department)
        return
    try:
        validate_upload(mime,data)
    except HTTPException as error:
        problem = error.detail
    params = {"department":department,"sha":hashlib.sha256(data).hexdigest(),
              "path":f"{department}/{filename}","mime":mime,"size":len(data)}
    # Serialize duplicate copies across workers until parsing finishes. The database
    # row is committed before parsing to permit recovery after a worker interruption.
    key = f"resume-share:{department}:{params['sha']}"
    with owner_engine.connect() as lock_connection:
        acquired = lock_connection.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key":key}).scalar_one()
        lock_connection.commit()
        if not acquired:
            return
        try:
            with owner_engine.begin() as connection:
                version = _register(connection, params, data, filename, problem)
            if version is not None:
                try:
                    process_resume.run(version)
                except Exception:
                    with owner_engine.begin() as connection:
                        connection.execute(text("""
                            UPDATE resume_dropfile SET status='failed',fail_reason='简历解析失败',processed_at=now()
                            WHERE department_id=:department AND sha256=:sha AND status='processing'
                              AND EXISTS (SELECT 1 FROM resume_version WHERE id=:version AND status='failed')
                        """), {**params,"version":version})
                    raise
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key":key})
            lock_connection.commit()
