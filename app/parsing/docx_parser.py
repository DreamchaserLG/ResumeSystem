"""DOCX 解析（03 §2）：按 body 元素顺序提取段落与表格，保留表头-行关系。

内嵌图片由 pipeline 提取后交给照片检测；旧版 DOC 由 LibreOffice 转换后进入同一流程。
"""
import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.extraction import LABELS, norm
from app.parsing.ir import Page, ParsedDocument, Span

PARSER_VERSION = "docx-v2"


def _xml_text(element) -> str:
    """Read visible Word text, including DrawingML/text-box descendants.

    ``python-docx``'s ``Paragraph.text`` deliberately ignores text nested in
    shapes.  Resume templates frequently place the name, contact summary and
    section labels in those shapes, so relying on it alone silently drops a
    sizeable part of the document.
    """
    return "".join(
        node.text or ""
        for node in element.iter()
        if node.tag.rsplit("}", 1)[-1] == "t"
    ).strip()


def _paragraph_text(paragraph: Paragraph) -> str:
    plain = paragraph.text.strip()
    expanded = _xml_text(paragraph._element)
    return expanded if len(expanded) > len(plain) else plain


def parse_docx(path: str) -> ParsedDocument:
    d: DocxDocument = docx.Document(path)
    pd = ParsedDocument(
        pages=[Page(1, 0, 0)],
        meta={"mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "parser_version": PARSER_VERSION, "embedded_images": []},
    )
    order = 0
    para_idx = 0
    table_idx = 0

    # Header text is often where a resume template keeps the candidate name
    # and contact block.  De-duplicate linked headers across Word sections.
    seen_header_footer: set[tuple[str, str]] = set()
    for section_idx, section in enumerate(d.sections):
        for area_name, area in (("header", section.header), ("footer", section.footer)):
            for area_para_idx, para in enumerate(area.paragraphs):
                text = _paragraph_text(para)
                marker = (area_name, text)
                if not text or marker in seen_header_footer:
                    continue
                seen_header_footer.add(marker)
                order += 1
                pd.add_span(
                    Span(
                        span_kind="text",
                        raw_text=text,
                        locator={f"docx_{area_name}": [section_idx, area_para_idx]},
                        extraction_method=f"docx_{area_name}",
                        reading_order=order,
                    )
                )

    for element in d.element.body:
        tag = element.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(element, d)
            text = _paragraph_text(para)
            if not text:
                para_idx += 1
                continue
            order += 1
            style = (para.style.name or "").lower() if para.style is not None else ""
            kind = "heading" if ("heading" in style or "title" in style) else "text"
            pd.add_span(
                Span(
                    span_kind=kind,
                    raw_text=text,
                    locator={"docx_para": para_idx},
                    extraction_method="docx_paragraph",
                    reading_order=order,
                )
            )
            para_idx += 1
        elif tag == "tbl":
            table = Table(element, d)
            table_idx += 1
            headers = []
            for r, row in enumerate(table.rows):
                cells = []
                for cell in row.cells:
                    plain = cell.text.strip()
                    expanded = _xml_text(cell._tc)
                    cells.append(expanded if len(expanded) > len(plain) else plain)
                if not any(cells):
                    continue
                order += 1
                # 表头行保留 + 行内容成 span（locator 带行列，可回跳到 Word 表格位置）
                if r == 0 and len(cells) >= 2 and sum(norm(cell) in LABELS for cell in cells) >= 2:
                    headers = cells
                if headers and r:
                    prefix = " | ".join(f"{header}: {value}" for header, value in zip(headers, cells) if value)
                else:
                    prefix = " | ".join(cells)
                pd.add_span(
                    Span(
                        span_kind="table",
                        raw_text=prefix,
                        locator={"docx_table": [r, len(cells)], "table_index": table_idx},
                        extraction_method="docx_table",
                        reading_order=order,
                    )
                )

    # 内嵌图片：记录 part 名，pipeline 提取二进制后走照片检测
    for rel in d.part.rels.values():
        if "image" in rel.reltype and not rel.is_external:
            pd.meta["embedded_images"].append(str(rel.target_part.partname).lstrip("/"))

    pd.finalize()
    return pd


def extract_embedded_images(path: str) -> list[tuple[str, bytes]]:
    """返回 [(partname, bytes)]，供照片检测使用。"""
    import io

    d = docx.Document(path)
    out = []
    for rel in d.part.rels.values():
        if "image" in rel.reltype and not rel.is_external:
            blob = rel.target_part.blob
            try:
                from PIL import Image

                Image.open(io.BytesIO(blob)).verify()  # 仅验证可读
            except Exception:
                continue
            out.append((str(rel.target_part.partname).lstrip("/"), blob))
    return out
