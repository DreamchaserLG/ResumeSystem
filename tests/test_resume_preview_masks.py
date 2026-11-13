"""Visual masking keeps neighbouring text and only serves original photo to authorized callers."""
from io import BytesIO

import pymupdf
from PIL import Image

from app.parsing.ir import ParsedDocument, Span
from app.parsing.preview import render_preview


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
