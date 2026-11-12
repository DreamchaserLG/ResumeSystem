"""解析流水线（03 §1/§7）：格式分派 → IR → 照片检测 → 脱敏预览 → emit_spans。

OCR 置信度路由（03 §5）：≥0.90 直接；0.75–0.90 关键字段低置信 issue；<0.75 span 保留但标低置信。
"""
import hashlib
from statistics import median
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from app.parsing import photo_detect
from app.parsing.docx_parser import parse_docx
from app.parsing.ir import ParsedDocument, Span
from app.parsing.pdf_parser import parse_pdf

LOW_CONF_THRESHOLD = 0.75
HIGH_CONF_THRESHOLD = 0.90


class ParserNotSupported(Exception):
    pass


def parse_document(path: str, mime_type: str) -> ParsedDocument:
    """格式分派入口。DOC 旧格式需先经 LibreOffice 转换（部署侧预置，M3 v1 明确报不支持）。"""
    mt = (mime_type or "").lower()
    ext = os.path.splitext(path)[1].lower()
    if mt == "application/pdf" or ext == ".pdf":
        return parse_pdf(path)
    if (
        mt in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",)
        or ext == ".docx"
    ):
        return parse_docx(path)
    if mt.startswith("image/") or ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        return _parse_image(path, mt or f"image/{ext.lstrip('.')}")
    if ext == ".doc" or mt == "application/msword":
        with TemporaryDirectory(prefix="resume-word-") as directory:
            subprocess.run([sys.executable, "-m", "app.parsing.doc_convert", path, directory],
                           timeout=90, check=True, capture_output=True)
            converted = Path(directory) / (Path(path).stem + ".docx")
            document = parse_docx(str(converted))
            from PIL import Image
            import io

            from app.parsing.docx_parser import extract_embedded_images

            for _, blob in extract_embedded_images(str(converted)):
                with Image.open(io.BytesIO(blob)) as image:
                    document.spans.extend(photo_detect.detect_photo_regions(image.convert("RGB")))
            document.meta["has_photo"] = any(span.span_kind == "photo" for span in document.spans)
            document.meta["parser_version"] = "libreoffice-doc+docx-v1"
            document.finalize()
            return document
    raise ParserNotSupported(f"不支持的文件类型: {mime_type or ext}")


def _parse_image(path: str, mime: str) -> ParsedDocument:
    from PIL import Image, ImageDraw

    from app.parsing.ir import Page
    from app.parsing.ocr import ocr_page

    pd = ParsedDocument(meta={"mime_type": mime, "parser_version": "ocr-v1"})
    with Image.open(path) as im:
        W, H = im.size
        rgb = im.convert("RGB")
    pd.pages.append(Page(1, W, H))

    from app.parsing.ocr import get_layout_regions

    regions = get_layout_regions(path)
    figures = [r["bbox"] for r in regions if r["label"] in {"image", "figure"}]
    text_boxes = [r["bbox"] for r in regions if r["label"] not in {"image", "figure"}]
    photo_spans = photo_detect.detect_photo_regions(rgb, figures, text_boxes) if figures else []
    if photo_spans:
        draw = ImageDraw.Draw(rgb)
        for region in photo_spans:
            draw.rectangle(region.locator["bbox"], fill="white")
        with TemporaryDirectory(prefix="resume-ocr-") as directory:
            masked = Path(directory) / "page.png"
            rgb.save(masked)
            result = ocr_page(str(masked))
    else:
        result = ocr_page(path)
        photo_spans = photo_detect.detect_photo_regions(rgb, figures, [l["bbox"] for l in result["lines"]])
        result["lines"] = [line for line in result["lines"] if not any(
            photo_detect._text_overlap_ratio(tuple(line["bbox"]), [region.locator["bbox"]]) > .5
            for region in photo_spans
        )]
    line_height = median([line["bbox"][3] - line["bbox"][1] for line in result["lines"]]) if result["lines"] else 0
    for order, line in enumerate(result["lines"], 1):
        box = line["bbox"]
        prominent = box[1] < H * .22 and box[3] - box[1] > max(line_height * 1.25, 15)
        pd.add_span(Span("heading" if prominent else "text", line["text"], {"page": 1, "bbox": line["bbox"]},
                         "ocr", ocr_confidence=round(line["score"], 4), reading_order=order))
    for ps in photo_spans:
        pd.add_span(ps)
    pd.meta["has_photo"] = bool(photo_spans)
    pd.finalize()
    return pd


def detect_photo_on_pdf_page(pdf_path: str, page_no: int, zoom: float = 2.0) -> list[Span]:
    """PDF 页照片检测：渲染位图 → 图片对象候选框 → 人脸/先验判定。"""
    import io

    import pymupdf
    from PIL import Image

    with pymupdf.open(pdf_path) as doc:
        page = doc[page_no - 1]
        rects = list(_image_rects(page))
        if not rects:
            return []
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), annots=False)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        def scale(box):
            return [value*zoom for value in (pymupdf.Rect(box)*page.rotation_matrix)]
        text_boxes = [scale(block[:4]) for block in page.get_text("blocks") if block[-1] == 0]
        return photo_detect.detect_photo_regions(img, figure_regions=[scale(rect) for rect in rects],
                                                text_lines=text_boxes, page_no=page_no)


def _image_rects(page):
    """页面图片对象的显示矩形。"""
    try:
        for info in page.get_image_info():
            yield info["bbox"]
    except Exception:
        return []


def enrich_pdf_with_ocr(pd: ParsedDocument, pdf_path: str) -> ParsedDocument:
    """混合 PDF：对 meta.ocr_pages 中的页走 OCR，追加文本 span 与照片检测。"""
    if not pd.meta.get("ocr_pages"):
        return pd
    import pymupdf

    with pymupdf.open(pdf_path) as doc, TemporaryDirectory(prefix="resume-pdf-ocr-") as directory:
        for pno in pd.meta["ocr_pages"]:
            image = Path(directory) / f"page-{pno}.png"
            doc[pno-1].get_pixmap(dpi=200).save(str(image))
            page = _parse_image(str(image), "image/png")
            for span in page.spans:
                span.locator["page"] = pno
                span.locator["ocr_dpi"] = 200
                pd.spans.append(span)
    pd.meta["has_photo"] = pd.meta.get("has_photo", False) or any(
        s.span_kind == "photo" for s in pd.spans
    )
    pd.finalize()
    return pd


def emit_spans(pd: ParsedDocument, version_id: int, owner_conn) -> int:
    """落库 resume_source_span（仅 text/heading/table；photo span 一并落库供证据链引用）。"""
    from sqlalchemy import text

    n = 0
    for s in pd.spans:
        owner_conn.execute(
            text(
                "INSERT INTO resume_source_span (version_id, span_no, span_kind, raw_text,"
                " locator, extraction_method, is_sensitive, ocr_confidence, reading_order)"
                " VALUES (:v, :no, :kind, :rt, CAST(:loc AS jsonb), :em, :sen, :conf, :ro)"
            ),
            {
                "v": version_id,
                "no": s.span_no,
                "kind": s.span_kind,
                "rt": s.raw_text,
                "loc": __import__("json").dumps(s.locator),
                "em": s.extraction_method,
                "sen": s.is_sensitive,
                "conf": s.ocr_confidence,
                "ro": s.reading_order,
            },
        )
        n += 1
    return n


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def low_confidence_keys(pd: ParsedDocument) -> list[dict]:
    """置信度路由：返回低置信行摘要（落 resume_field_issue 由 M4 抽取阶段关联字段）。"""
    out = []
    for s in pd.spans:
        if s.ocr_confidence is not None and s.ocr_confidence < HIGH_CONF_THRESHOLD:
            out.append(
                {
                    "span_no": s.span_no,
                    "text": s.raw_text,
                    "confidence": s.ocr_confidence,
                    "below_hard": s.ocr_confidence < LOW_CONF_THRESHOLD,
                }
            )
    return out
