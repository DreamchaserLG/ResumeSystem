"""M3 解析模块测试：IR 一致性、格式分派、照片检测、脱敏预览。

图片 OCR 较慢，单独标记 slow（pytest -m slow 或全量跑）。
"""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.parsing import pipeline, photo_detect
from app.parsing.docx_parser import parse_docx
from app.parsing.ir import ParsedDocument, Span
from app.parsing.pdf_parser import parse_pdf

def _make_photo_pdf(tmp_path, with_avatar: bool):
    """生成测试 PDF：标题 + 段落；with_avatar 时嵌入人形图片。"""
    import pymupdf

    path = str(tmp_path / ("avatar.pdf" if with_avatar else "plain.pdf"))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Example Applicant", fontsize=20)
    page.insert_text((72, 110), "Research Assistant", fontsize=12)
    page.insert_text((72, 140), "Work Experience", fontsize=14)
    page.insert_text((72, 170), "Example Robotics 2018.01 - 2020.09", fontsize=11)
    if with_avatar:
        img = Image.new("RGB", (120, 160), (200, 180, 170))
        d = Image.new("RGB", (60, 60), (120, 100, 90))  # 人脸色块
        img.paste(d, (30, 20))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page.insert_image(pymupdf.Rect(430, 50, 550, 210), stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def test_ir_dataclass_contract():
    pd = ParsedDocument(meta={})
    pd.add_span(Span("text", "hello", {"page": 1}, "pdf_text", reading_order=2))
    pd.add_span(Span("photo", "accidental", {"page": 1}, "layout_detection", reading_order=1))
    pd.finalize()
    assert [s.span_no for s in pd.spans] == [1, 2]
    photo = next(s for s in pd.spans if s.span_kind == "photo")
    assert photo.raw_text is None  # photo span 恒定无文本（finalize 强制）


def test_pdf_text_parsing(tmp_path):
    path = _make_photo_pdf(tmp_path, with_avatar=True)
    pd = parse_pdf(path)
    assert pd.pages[0].page_no == 1
    texts = " ".join(s.raw_text or "" for s in pd.spans if s.span_kind in ("text", "heading"))
    assert "Example Applicant" in texts
    assert "Example Robotics" in texts
    assert all(s.locator.get("page") == 1 for s in pd.spans)
    # 阅读顺序单调递增
    orders = [s.reading_order for s in pd.spans]
    assert orders == sorted(orders)


def test_pdf_photo_detection(tmp_path):
    """PDF 内嵌人形图 → has_photo；无图版本 → 无 photo span。"""
    with_avatar = _make_photo_pdf(tmp_path, with_avatar=True)
    plain = _make_photo_pdf(tmp_path, with_avatar=False)
    pd1 = parse_pdf(with_avatar)
    photo_spans = pipeline.detect_photo_on_pdf_page(with_avatar, 1)
    pd1.spans.extend(photo_spans)
    pd1.finalize()
    pd2 = parse_pdf(plain)
    assert bool(photo_spans) is True
    assert all(s.span_kind != "photo" for s in pd2.spans)


def test_docx_parsing(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("示例候选人")
    table = doc.add_table(rows=0, cols=2)
    for label, value in (("岗位", "软件工程师"), ("学历", "本科"), ("工作单位", "示例科技公司")):
        left, right = table.add_row().cells
        left.text, right.text = label, value
    graphic = io.BytesIO()
    Image.new("RGB", (100, 120), "grey").save(graphic, format="PNG")
    graphic.seek(0)
    doc.add_picture(graphic)
    path = tmp_path / "synthetic.docx"
    doc.save(path)

    pd = parse_docx(str(path))
    assert any(s.locator.get("docx_para") is not None for s in pd.spans)
    table_spans = [s for s in pd.spans if s.span_kind == "table"]
    assert table_spans and all(isinstance(s.locator.get("docx_table"), list) for s in table_spans)
    assert "示例科技公司" in " ".join(s.raw_text or "" for s in table_spans)
    assert pd.meta["embedded_images"]


def test_photo_detect_on_synthetic_avatar():
    """合成头像（角落人形色块）→ photo；纯文本图 → graphic/无。"""
    img = Image.new("RGB", (600, 800), (255, 255, 255))
    avatar = Image.new("RGB", (110, 140), (190, 170, 160))
    face = Image.new("RGB", (50, 55), (110, 90, 80))
    avatar.paste(face, (30, 25))
    img.paste(avatar, (450, 40))
    spans = photo_detect.detect_photo_regions(img, figure_regions=[[450, 40, 560, 180]], page_no=1)
    assert len(spans) == 1
    assert spans[0].span_kind == "photo"
    assert spans[0].raw_text is None
    assert spans[0].locator["bbox"] == [450, 40, 560, 180]
    assert photo_detect.detect_photo_regions(Image.new("RGB", (600, 800), "white")) == []


def test_masked_preview_blurs_only_photo_region():
    img = Image.new("RGB", (400, 500), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    preview = photo_detect.build_masked_preview(buf.getvalue(), [[300, 30, 380, 160]], block=10)
    out = Image.open(io.BytesIO(preview))
    assert out.size == (400, 500)
    # 遮蔽区域内部应为纯色块（像素化后人脸色差异消失）
    region_colors = set()
    for x in range(305, 375, 5):
        for y in range(35, 155, 5):
            region_colors.add(out.getpixel((x, y)))
    assert len(region_colors) <= 4  # 像素化后颜色种类极少


def test_unsupported_type_rejected():
    with pytest.raises(pipeline.ParserNotSupported):
        pipeline.parse_document("nope.xyz", "application/x-unknown")


@pytest.mark.slow
def test_image_ocr_pipeline(tmp_path):
    """Check native OCR with a generated resume; no private test files are needed."""
    image = Image.new("RGB", (1120, 1400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    for i, line in enumerate((
        "Profile", "Name: Example Candidate", "Target role: Software Engineer",
        "Education", "School: Example University", "Degree: Bachelor",
        "Work Experience", "Company: Example Technology Ltd.",
        "Dates: 2020.01-2025.12", "Go and software development",
    )):
        draw.text((90, 75 + i * 85), line, font=font, fill="black")
    path = tmp_path / "synthetic.png"
    image.save(path)

    pd = pipeline.parse_document(str(path), "image/png")
    text_spans = [s for s in pd.spans if s.span_kind in ("text", "heading")]
    assert len(text_spans) >= 8
    assert all(s.extraction_method == "ocr" for s in text_spans)
    assert all(s.ocr_confidence is not None and 0 <= s.ocr_confidence <= 1 for s in text_spans)
    assert "Example Candidate" in " ".join(s.raw_text or "" for s in text_spans)
