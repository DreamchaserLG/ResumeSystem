"""Parse short-lived job uploads on the existing document worker."""
import base64
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from worker.celery_app import celery_app
from app.core.cache import get_redis
from app.core.job_import import FIELDS, job_draft

logger = logging.getLogger(__name__)


def document_lines(data, extension, mime):
    if extension == ".txt":
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = data.decode("gb18030")
        if "\x00" in decoded:
            raise ValueError("无法读取文本编码，请另存为 UTF-8 后重试")
        return decoded.splitlines()
    from app.parsing.pipeline import enrich_pdf_with_ocr, parse_document

    with TemporaryDirectory(prefix="rs-job-") as directory:
        path = Path(directory) / ("input" + extension)
        path.write_bytes(data)
        if extension == ".pdf":
            import pymupdf
            with pymupdf.open(str(path)) as pdf:
                if len(pdf) > 10:
                    raise ValueError("单份岗位 PDF 不得超过 10 页")
        document = parse_document(str(path), mime)
        if extension == ".pdf":
            document = enrich_pdf_with_ocr(document, str(path))
        lines = []
        for span in document.spans:
            if span.span_kind == "photo" or not span.raw_text:
                continue
            if "docx_table" in span.locator:
                cells = span.raw_text.split(" | ")
                if len(cells) == 2 and cells[0].rstrip("：:").strip() in FIELDS:
                    lines.append(cells[0].rstrip("：:") + "：" + cells[1].replace("\n", " "))
                    continue
                if len(cells) > 1 and all(":" in cell or "：" in cell for cell in cells):
                    lines.extend(cell.replace("\n", " ") for cell in cells)
                    continue
            lines.extend(span.raw_text.splitlines())
        return lines


@celery_app.task(name="worker.parse_job_file", ignore_result=True, soft_time_limit=175, time_limit=190)
def parse_job_file(job_import_id):
    key = f"rs:job-import:{job_import_id}"
    cache = get_redis()
    raw = cache.get(key)
    if not raw:
        return
    pending = json.loads(raw)
    if pending.get("status") != "pending":
        return
    user_id = pending["user_id"]
    result = {"user_id": user_id, "status": "failed"}
    try:
        lines = document_lines(base64.b64decode(pending["data"]), pending["extension"], pending["mime"])
        result = {"user_id": user_id, "status": "ready", "draft": job_draft(lines)}
    except UnicodeDecodeError:
        result["error"] = "无法读取文本编码，请另存为 UTF-8 后重试"
    except ValueError as exc:
        # Only surface errors we generated from explicit import rules.
        if str(exc).startswith(("文档含多个岗位", "没有识别到", "单份岗位 PDF", "无法读取文本编码")):
            result["error"] = str(exc)
    except Exception as exc:
        logger.warning("Job import processing failed: %s", type(exc).__name__)
    if result["status"] == "failed":
        result.setdefault("error", "无法解析该岗位文件，请检查文件或手动新建")
    cache.set(key, json.dumps(result, ensure_ascii=False), ex=900, xx=True)
