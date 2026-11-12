"""Redis 连接（缓存与单飞锁共用）。"""
import redis

from app.core.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_connect_timeout=2
        )
    return _client


def ping_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
