"""健康检查与系统状态路由。"""
from fastapi import APIRouter

from app.core.cache import ping_redis
from app.core.config import get_settings
from app.core.db import ping_db_detailed

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict:
    db = ping_db_detailed()
    redis_ok = ping_redis()
    ok = db.get("ok", False) and redis_ok
    return {
        "ok": ok,
        "app": get_settings().app_name,
        "env": get_settings().app_env,
        "db": db,
        "redis": {"ok": redis_ok},
    }
