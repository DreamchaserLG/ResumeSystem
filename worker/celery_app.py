"""Celery 应用（异步任务队列，M3 起承载解析/OCR/抽取流水线）。"""
import os

from celery import Celery

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

# 导入任务模块，保证任务注册
from worker.tasks import parse, ping  # noqa: E402,F401
