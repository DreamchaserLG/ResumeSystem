"""数据库访问：SQLAlchemy engine + 请求级会话。

RLS 约定（02 §2.2）：业务查询在一个显式事务内，先 set_config('app.*', ..., true)
再执行查询；本文件提供 with_rls_session(ctx) 帮助函数，ctx 为授权上下文。
"""
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(url: str | None = None):
    s = url or get_settings().app_database_url
    # psycopg3 驱动
    return create_engine(s.replace("postgresql://", "postgresql+psycopg://"), pool_pre_ping=True, hide_parameters=True)


engine = build_engine()  # rs_app：非 owner 角色，RLS 生效
owner_engine = build_engine(get_settings().database_url)  # 迁移/种子专用
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def rls_session(
    departments: list[int] | None = None,
    client_id: int | None = None,
    application_ids: list[int] | None = None,
    user_id: int | None = None,
) -> Iterator[Session]:
    """开启一个带 RLS 上下文的事务会话。

    ctx 键映射 02 §2.2 的会话变量；一律事务级 set_config，提交/回滚后自动清除。
    """
    s = SessionLocal()
    try:
        trans = s.begin()
        set_map: dict[str, str] = {}
        if departments:
            set_map["app.departments"] = ",".join(str(d) for d in departments)
        if client_id is not None:
            set_map["app.client_id"] = str(client_id)
        if application_ids:
            set_map["app.application_ids"] = ",".join(str(a) for a in application_ids)
        if user_id is not None:
            set_map["app.user_id"] = str(user_id)
        for key, val in set_map.items():
            s.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": val})
        yield s
        trans.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def ping_db() -> bool:
    try:
        with engine.connect() as cn:
            cn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def ping_db_detailed() -> dict[str, Any]:
    """健康检查详情：数据库版本、迁移状态。"""
    try:
        with engine.connect() as cn:
            version = cn.execute(text("SELECT version()")).scalar_one()
            applied = cn.execute(
                text("SELECT count(*) FROM schema_migrations")
            ).scalar_one()
            return {"ok": True, "pg": version.split(",")[0], "migrations": applied}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
