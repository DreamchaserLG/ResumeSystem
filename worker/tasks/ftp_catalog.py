"""岗位目录的手动与定时同步共用同一任务。"""
import logging
from ftplib import Error

from celery import shared_task
from sqlalchemy import text

from app.core import ftp_catalog, ftp_source
from app.core.db import owner_engine
from worker.tasks.parse import process_resume

logger = logging.getLogger(__name__)
QUEUE = "ftp_catalog"


def _queue_parses():
    with owner_engine.begin() as connection:
        if not ftp_catalog.lock_catalog(connection):
            return 0
        versions = connection.execute(text("""
            SELECT f.version_id FROM ftp_catalog_file f
            JOIN resume_version v ON v.id=f.version_id AND v.status='parsing'
            JOIN application a ON a.id=f.application_id AND a.deleted_at IS NULL
              AND f.version_id=coalesce(a.pending_version_id,a.submitted_version_id)
            WHERE f.present AND f.managed_application
            GROUP BY f.version_id
            HAVING max(f.parse_queued_at) IS NULL OR max(f.parse_queued_at)<now()-interval '24 hours'
        """)).scalars().all()
        for version in versions:
            process_resume.apply_async(args=[version], retry=False)
            connection.execute(text("""
                UPDATE ftp_catalog_file SET parse_queued_at=now() WHERE version_id=:version
            """), {"version": version})
        return len(versions)


@shared_task(name="worker.sync_ftp_job_catalog", queue=QUEUE, acks_late=True, track_started=True)
def sync_ftp_catalog(actor_id=None):
    try:
        result = ftp_catalog.synchronize(actor_id)
        if result is None:
            return {"status": "busy"}
    except ftp_source.CatalogError as error:
        logger.warning("FTP 目录同步中止：%s", error)
        return {"status": "failed", "message": str(error)}
    except (Error, OSError, EOFError, ValueError, UnicodeError) as error:
        logger.warning("FTP 目录同步失败，异常类型：%s", type(error).__name__)
        return {"status": "failed", "message": "FTP 读取失败，请检查连接和目录权限后重试"}
    except Exception as error:
        logger.error("FTP 目录入库失败，异常类型：%s", type(error).__name__)
        return {"status": "failed", "message": "目录入库失败，本次变更已回滚"}
    try:
        result["queued"] = _queue_parses()
    except Exception as error:
        logger.error("FTP 简历进入解析队列失败，异常类型：%s", type(error).__name__)
        return {"status": "complete", **result, "message": "目录已同步，暂未提交简历解析，将在下次同步重试"}
    return {"status": "complete", **result}
