"""审计事件落库（02 §6 最小集）。

审计是系统级行为，走 owner 连接：audit_log 的 SELECT 策略仅超管可见，
若用 rs_app 无上下文连接写入，写入虽成功但随后任何回读都会被 RLS 隐藏。
"""
import json
import logging
from typing import Any

from sqlalchemy import text

from app.core.db import owner_engine

logger = logging.getLogger("audit")


def write_audit(
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: str,
    result: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    """写审计日志；审计失败不阻断业务，仅记录到应用日志。"""
    try:
        with owner_engine.begin() as cn:
            cn.execute(
                text(
                    "INSERT INTO audit_log (actor_id, action, target_type, target_id, result, detail)"
                    " VALUES (:actor, :action, :tt, :tid, :result, :detail)"
                ),
                {
                    "actor": actor_id,
                    "action": action,
                    "tt": target_type,
                    "tid": str(target_id),
                    "result": result,
                    "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
                },
            )
    except Exception:
        logger.exception("audit write failed: %s", action)
