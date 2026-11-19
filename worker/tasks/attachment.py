"""对话附件解析（11 号扩展）：pdf/docx/doc/图片 OCR → 纯文本 → Redis，TTL 2h。

api 容器不含解析依赖（pymupdf/python-docx/paddleocr 均在 worker 镜像），
故由 celery 任务在 worker 侧完成解析。
"""
import json
import logging

from celery import shared_task

from app.core.cache import get_redis
from app.core.storage import open_file

logger = logging.getLogger("worker.attachment")

TTL_SECONDS = 7200
MAX_CHARS = 20000


def _key(attachment_id: str) -> str:
    return f"rs:attach:{attachment_id}"


@shared_task(name="worker.parse_attachment", max_retries=1)
def parse_attachment(payload: dict):
    attachment_id = payload["attachment_id"]
    name = payload.get("original_name") or "附件"
    try:
        path = open_file(payload["object_key"])
        if path.lower().endswith((".txt", ".md")):
            # 纯文本直读：parse_document 只覆盖 pdf/docx/doc/图片
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        else:
            from app.parsing.pipeline import parse_document

            document = parse_document(path, payload.get("mime_type") or "")
            text = "\n".join(span.raw_text for span in document.spans if span.raw_text).strip()
        if not text:
            state = {"status": "failed", "name": name, "error": "未能从文件中提取到文本"}
        else:
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + "\n…（内容过长，已截断）"
            state = {"status": "ready", "name": name, "chars": len(text), "text": text}
    except Exception as error:  # noqa: BLE001 —— 任何解析失败都落为 failed 状态
        logger.info("attachment parse failed: %s", error)
        state = {"status": "failed", "name": name, "error": str(error)[:200]}
    try:
        get_redis().set(_key(attachment_id), json.dumps(state, ensure_ascii=False), ex=TTL_SECONDS)
    except Exception:  # noqa: BLE001 —— Redis 不可用时结果丢失，前端轮询按过期提示
        logger.warning("attachment result not persisted: redis unavailable")
    return state["status"]
