"""解析任务（03 §4/§5）：parse → photo → preview → emit_spans → 原子发布。

失败路径：version.status=failed + fail_reason，申请保留在 HR 待处理列表（01 设计）。
"""
import logging
import subprocess

from celery import shared_task
from sqlalchemy import text

from app.core.db import owner_engine
from app.core.extraction import private_card_fields
from app.core.resume_pipeline import insert, prepare_extraction, publish, write_extraction
from app.core.storage import save_preview
from app.core.workflow import ReviewReplayError, replay_reviews

logger = logging.getLogger("worker.parse")


@shared_task(name="worker.process_resume", max_retries=1)
def process_resume(version_id: int):
    rendered_keys = []
    try:
        from app.parsing import photo_detect, pipeline
        from app.parsing.ir import ParsedDocument

        with owner_engine.connect() as cn:
            doc = cn.execute(
                text(
                    "SELECT d.object_key, d.mime_type, v.status"
                    " FROM resume_version v JOIN resume_document d ON d.id = v.document_id"
                    " WHERE v.id = :i"
                ),
                {"i": version_id},
            ).mappings().one()
        if doc["status"] == "published":
            return {"ok": True, "already_published": True}
        object_key, mime_type = doc["object_key"], doc["mime_type"]
        path = pipeline_open_file(object_key)

        # ① 解析（格式分派）
        pd: ParsedDocument = pipeline.parse_document(path, mime_type)
        # ② 混合 PDF 扫描页 OCR
        pd = pipeline.enrich_pdf_with_ocr(pd, path)
        # ③ PDF 各页照片检测（扫描页已在 enrich 中处理）
        if mime_type == "application/pdf":
            import pymupdf

            with pymupdf.open(path) as pdf:
                for pno in range(1, len(pdf) + 1):
                    if pno in pd.meta.get("ocr_pages", []):
                        continue
                    pd.spans.extend(pipeline.detect_photo_on_pdf_page(path, pno))
            pd.finalize()
        # ④ DOCX 内嵌图片照片检测
        if mime_type.endswith("wordprocessingml.document"):
            from PIL import Image
            import io

            from app.parsing.docx_parser import extract_embedded_images

            for _, blob in extract_embedded_images(path):
                try:
                    img = Image.open(io.BytesIO(blob)).convert("RGB")
                except Exception:
                    continue
                pd.spans.extend(photo_detect.detect_photo_regions(img, None, 1))
            pd.meta["has_photo"] = any(s.span_kind == "photo" for s in pd.spans)
            pd.finalize()

        # ⑤ 脱敏预览（图片简历整页 / PDF 首页渲染；DOCX v1 无预览图）
        pd.finalize()
        with owner_engine.connect() as cn:
            allowed = set(cn.execute(text("""
                SELECT split_part(f.field_key,'.',2) FROM field_usage_policy f JOIN search_state st USING(policy_version)
                WHERE f.allowed AND f.purpose='preview' AND f.field_key IN ('profile.gender','profile.address','profile.avatar')
            """)).scalars())
            card = private_card_fields(pd, allowed)
            records = prepare_extraction(cn, pd)
        if not any(s.raw_text and not s.is_sensitive and s.raw_text.strip("█ ") for s in pd.spans):
            raise ValueError("未取得可用文本")
        from app.parsing.preview import render_preview
        try:
            pages, avatar = render_preview(path, mime_type, pd, "avatar" in allowed)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            logger.warning("Page preview could not be rendered for version %s", version_id)
            pages, avatar = [], None

        # ⑥ 原子发布：spans + has_photo + version.published + application.active_version
        with owner_engine.begin() as cn:
            cn.execute(text("""
                SELECT id FROM application WHERE pending_version_id=:v OR submitted_version_id=:v FOR UPDATE
            """), {"v": version_id}).all()
            status = cn.execute(text("SELECT status FROM resume_version WHERE id=:v FOR UPDATE"), {"v": version_id}).scalar_one_or_none()
            if status is None or status == "published":
                return {"ok": True, "already_processed": True}
            pipeline.emit_spans(pd, version_id, cn)
            write_extraction(cn, version_id, records)
            replay_reviews(cn, version_id)
            page_rows = []
            for index, content, photo in pages:
                key = save_preview(content, f"resume-version-{version_id}-{index}")
                rendered_keys.append(key)
                photo_key = save_preview(photo, f"resume-photo-version-{version_id}-{index}") if photo else None
                if photo_key:
                    rendered_keys.append(photo_key)
                page_rows.append({"version_id": version_id, "page_no": index, "object_key": key, "photo_object_key":photo_key})
            avatar_key = save_preview(avatar, f"resume-avatar-{version_id}") if avatar else None
            if avatar_key:
                rendered_keys.append(avatar_key)
            if card:
                insert(cn, "resume_card_private", [{"version_id": version_id, "gender": card.get("gender"), "address": card.get("address")}])
            insert(cn, "resume_preview_page", page_rows)
            preview_key = page_rows[0]["object_key"] if page_rows else None
            cn.execute(text("""
                UPDATE resume_document SET has_photo = :photo
                WHERE id=(SELECT document_id FROM resume_version WHERE id=:v)
            """), {"photo": any(s.span_kind == "photo" for s in pd.spans), "v": version_id})
            cn.execute(text("UPDATE resume_version SET parser_version=:parser,preview_key=:preview,avatar_key=:avatar WHERE id=:v"),
                       {"parser": pd.meta.get("parser_version", "unknown"), "preview": preview_key, "avatar": avatar_key, "v": version_id})
            publish(cn, version_id)
        return {"ok": True, "spans": pd.meta.get("span_count"), "has_photo": pd.meta.get("has_photo")}

    except Exception as exc:  # noqa: BLE001
        if rendered_keys:
            from pathlib import Path
            from app.core.storage import storage_root
            for key in rendered_keys:
                (Path(storage_root()) / key).unlink(missing_ok=True)
        logger.error("process_resume failed for version %s (%s)", version_id, type(exc).__name__)
        reason = ("新解析未能匹配已复核字段，已保留当前版本。" if isinstance(exc, ReviewReplayError)
                  else "解析或字段抽取失败，请核对文件格式后重试")
        with owner_engine.begin() as cn:
            cn.execute(
                text("UPDATE resume_version SET status='failed', fail_reason=:r WHERE id = :i AND status <> 'published'"),
                {"r":reason,"i":version_id},
            )
        raise


def pipeline_open_file(object_key: str) -> str:
    from app.core.storage import open_file

    return open_file(object_key)
