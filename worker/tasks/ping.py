"""任务占位：M0 冒烟（DB/Redis 连通性）。"""
from celery import shared_task

from app.core.cache import ping_redis
from app.core.db import ping_db


@shared_task(name="worker.ping")
def ping() -> dict:
    return {"db": ping_db(), "redis": ping_redis(), "ok": ping_db() and ping_redis()}
