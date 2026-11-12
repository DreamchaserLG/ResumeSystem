"""文本 PDF 解析（03 §2）：PyMuPDF 文本块 + 页码 + bbox，恢复阅读顺序。

混合 PDF 按页判定：文本字符过少且含图 → 标记 ocr_pages，由 pipeline 对该页走 OCR。
"""
import pymupdf

from app.parsing.ir import Page, ParsedDocument, Span
from app.parsing.reading_order import by_columns

PARSER_VERSION = "pdf-text-v1"
MIN_CHARS_PER_PAGE = 40  # 低于此值且含图 → 疑似扫描页


def parse_pdf(path: str) -> ParsedDocument:
    doc = pymupdf.open(path)
    pd = ParsedDocument(
        meta={"mime_type": "application/pdf", "parser_version": PARSER_VERSION, "ocr_pages": []}
    )
    order = 0
    for page in doc:
        pno = page.number + 1
        pd.pages.append(Page(pno, int(page.rect.width), int(page.rect.height)))

        text_dict = page.get_text("dict")
        page_chars = sum(len(b.get("lines", [])) and sum(
            len(l["spans"]) and sum(len(sp["text"]) for sp in l["spans"])
            for l in b.get("lines", [])
        ) or 0 for b in text_dict.get("blocks", []))

        if page_chars < MIN_CHARS_PER_PAGE and page.get_images():
            # 疑似扫描页：记录坐标框架，正文走 OCR（pipeline 阶段填充）
            pd.meta["ocr_pages"].append(pno)
            continue

        for block in by_columns(text_dict.get("blocks", []), page.rect.width):
            if block.get("type") != 0:  # 0=text；图片块交给照片检测
                continue
            x0, y0, x1, y1 = block["bbox"]
            lines = []
            for line in block.get("lines", []):
                lines.append("".join(sp["text"] for sp in line["spans"]))
            text = "\n".join(lines).strip()
            if not text:
                continue
            kind = "heading" if _looks_heading(block, text) else "text"
            order += 1
            pd.add_span(
                Span(
                    span_kind=kind,
                    raw_text=text,
                    locator={"page": pno, "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]},
                    extraction_method="pdf_text",
                    reading_order=order,
                )
            )

    pd.spans.sort(key=lambda s: s.reading_order)
    pd.spans = [_compact_order(i, s) for i, s in enumerate(pd.spans, start=1)]
    pd.meta["has_photo"] = bool(pd.meta.get("ocr_pages")) or any(
        _page_has_image_objects(doc, p) for p in range(len(doc))
    )
    doc.close()
    return pd


def _compact_order(i: int, s: Span) -> Span:
    s.reading_order = i
    return s


def _looks_heading(block: dict, text: str) -> bool:
    """启发式：字号明显大 / 行短且无句末标点。"""
    sizes = [sp["size"] for l in block.get("lines", []) for sp in l["spans"]]
    if not sizes:
        return False
    avg = sum(sizes) / len(sizes)
    first_line = text.splitlines()[0]
    return avg >= 13 and len(first_line) <= 40 and not first_line.endswith(("。", ".", "；", ";", "，", ","))


def _page_has_image_objects(doc, page_idx: int) -> bool:
    try:
        return bool(doc[page_idx].get_images())
    except Exception:
        return False
