"""应用配置（pydantic-settings，环境变量驱动）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "智能简历筛选系统"

    # 迁移/种子用 owner 连接；应用业务连接用非 owner 的 rs_app（超管绕过 RLS，禁止业务使用）
    database_url: str = "postgresql://resume:resume@localhost:5434/resume"
    app_database_url: str = "postgresql://rs_app:rs_app123@localhost:5434/resume"
    redis_url: str = "redis://localhost:6380/0"
    celery_broker_url: str = "redis://localhost:6380/1"
    celery_result_backend: str = "redis://localhost:6380/2"

    storage_root: str = "./storage"
    session_ttl_seconds: int = 8 * 3600
    password_pbkdf2_rounds: int = 60000


@lru_cache
def get_settings() -> Settings:
    return Settings()
