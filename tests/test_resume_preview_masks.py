"""Visual masking keeps neighbouring text and only serves original photo to authorized callers."""
from io import BytesIO

import pymupdf
from PIL import Image

from app.core.extraction import masked, sensitive_regions
from app.parsing.ir import ParsedDocument, Span
from app.parsing.docx_parser import parse_docx
from app.parsing.preview import render_preview


def test_labelled_contact_masks_only_the_value():
    line = "电话：15047851696　邮箱：3200739511@qq.com　工作年限：20 年"
    result = masked(line)
    assert len(result) == len(line)
    assert result.startswith("电话：")
    assert result.endswith("工作年限：20 年")
    assert "█" * 11 in result and "█" * 17 in result
    assert [line[a:b] for a, b in sensitive_regions(line)] == ["15047851696", "3200739511@qq.com"]


def test_empty_private_field_is_not_masked():
    assert masked("电话：　邮箱：　工作年限：20 年") == "电话：　邮箱：　工作年限：20 年"


def test_header_line_keeps_unrelated_fields_visible(tmp_path):
    """A phone number must not paint a band across 工作年限 (the reported white line)."""
    path = tmp_path / "header.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=460, height=200)
        page.insert_text((25, 60), "电话：13800000001　工作年限：20 年", fontsize=11, fontname="china-s")
        pdf.save(path)
    parsed = ParsedDocument(spans=[])
    [(_, content, _)], _ = render_preview(str(path), "application/pdf", parsed, False)
    image = Image.open(BytesIO(content)).convert("RGB")
    grey = [(x, y) for y in range(image.height) for x in range(image.width)
            if image.getpixel((x, y)) == (237, 241, 245)]
    dark_right = [(x, y) for y in range(image.height) for x in range(image.width)
                  if sum(image.getpixel((x, y))) < 330]
    assert grey, "the phone value should still be masked"
    assert max(x for x, _ in grey) < 0.55 * image.width
    assert max(x for x, _ in dark_right) > max(x for x, _ in grey) + 40


def test_pdf_redacts_contact_glyphs_and_can_show_photo(tmp_path):
    path = tmp_path / "fixture.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=300)
        page.insert_text((25, 55), "Skills: Go         13800000001         Education: Bachelor", fontsize=10)
        photo = BytesIO()
        Image.new("RGB", (80, 80), "cornflowerblue").save(photo, format="PNG")
        page.insert_image(pymupdf.Rect(300, 100, 380, 180), stream=photo.getvalue())
        pdf.save(path)
    span = Span("photo", None, {"page":1,"bbox":[600,200,760,360]}, "layout_detection")
    parsed = ParsedDocument(spans=[span])
    [(no, masked, with_photo)], avatar = render_preview(str(path), "application/pdf", parsed, True)
    [(no, hidden, photo)], none = render_preview(str(path), "application/pdf", parsed, False)
    assert masked == hidden and with_photo and avatar and not photo and not none
    redacted = Image.open(BytesIO(masked)).convert("RGB")
    permitted = Image.open(BytesIO(with_photo)).convert("RGB")
    assert redacted.getpixel((650, 250)) == (237, 241, 245)
    assert permitted.getpixel((650, 250)) == (100, 149, 237)
    for image in (redacted, permitted):
        assert sum(r < 100 and g < 100 and b < 100 for r,g,b in image.crop((45,83,128,114)).getdata()) > 12
        assert (237,241,245) in set(image.crop((130,82,425,116)).getdata())
        assert sum(r < 100 and g < 100 and b < 100 for r,g,b in image.crop((245,83,500,114)).getdata()) > 12


def test_image_keeps_non_sensitive_lines_and_removes_sensitive_pixels(tmp_path):
    path = tmp_path / "fixture.png"
    Image.new("RGB",(400,300),"white").save(path)
    parsed = ParsedDocument(spans=[
        Span("text", "13800000001", {"bbox":[30,80,140,100]}, "ocr", ocr_confidence=.99),
        Span("text", "Education", {"bbox":[30,110,140,130]}, "ocr", ocr_confidence=.99),
    ])
    [(no, content, photo)], avatar = render_preview(str(path),"image/png",parsed,False)
    image = Image.open(BytesIO(content))
    assert image.getpixel((60,90)) == (237,241,245)
    assert image.getpixel((60,120)) == (255,255,255)


def test_docx_has_text_preview_without_libreoffice(tmp_path, monkeypatch):
    from docx import Document

    path = tmp_path / "resume.docx"
    document = Document()
    document.add_heading("示例候选人", level=1)
    for index in range(80):
        document.add_paragraph(f"项目经历 {index + 1}：负责系统设计、开发与交付。")
    document.save(path)
    parsed = parse_docx(str(path))
    monkeypatch.setattr("app.parsing.preview.shutil.which", lambda _: None)

    pages, avatar = render_preview(
        str(path),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        parsed,
        False,
    )

    assert len(pages) >= 2
    assert [number for number, _, _ in pages] == list(range(1, len(pages) + 1))
    assert all(Image.open(BytesIO(content)).getbbox() for _, content, _ in pages)
    assert avatar is None
