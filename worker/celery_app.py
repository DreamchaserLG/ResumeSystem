"""Celery 应用（异步任务队列，M3 起承载解析/OCR/抽取流水线）。"""
import os

from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

BROKER = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6380/1")
BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6380/2")

celery_app = Celery("resume_system", broker=BROKER, backend=BACKEND)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    worker_max_tasks_per_child=50,
    task_time_limit=900,          # 单任务硬超时 15 分钟（M3 解析流水线沿用）
    task_soft_time_limit=840,
)

settings = get_settings()
if settings.resume_drop_dir:
    celery_app.conf.beat_schedule = {
        "resume-library-daily": {
            "task": "worker.scan_resume_dropdir",
            "schedule": crontab(hour=settings.resume_drop_hour, minute=0),
        }
    }

# 导入任务模块，保证任务注册
from worker.tasks import attachment, job_import, parse, ping, scan_dropdir  # noqa: E402,F401
