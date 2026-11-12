"""Create browser-safe page images while retaining the document's page layout."""
import io
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from app.core.extraction import PRIVATE_LABELS, masked, norm
from app.parsing import pipeline

MAX_PAGES = 40
MAX_SIDE = 1800


def _private(text):
    return masked(text) != text or norm(text.rstrip("：: ")) in PRIVATE_LABELS | {
        "phone": "", "mobile": "", "联系电话": "", "手机": "", "电话": "", "email": "", "邮箱": "",
        "婚姻状况": "", "婚育情况": "", "身份证号": "",
    }


def _encoded(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _page_art(image, spans, boxes, avatar_allowed):
    from PIL import ImageDraw, ImageOps

    def clipped(box):
        x0, y0, x1, y1 = box
        return max(0, int(x0)-2), max(0, int(y0)-2), min(image.width, int(x1)+3), min(image.height, int(y1)+3)

    draw = ImageDraw.Draw(image)
    avatar = None
    covered_text = [clipped(box) for box in boxes] + [
        clipped(box) for span, box in spans
        if span.span_kind != "photo" and (span.is_sensitive or "█" in (span.raw_text or ""))
    ]

    def safe_crop(rect):
        x0,y0,x1,y1 = rect
        return (x1-x0 >= 30 and y1-y0 >= 30 and (x1-x0)*(y1-y0) <= image.width*image.height*.25
                and not any(min(x1,b[2]) > max(x0,b[0]) and min(y1,b[3]) > max(y0,b[1]) for b in covered_text))
    for span, box in spans:
        rect = clipped(box)
        if span.span_kind == "photo":
            if avatar_allowed and avatar is None and safe_crop(rect):
                crop = image.crop(rect)
                avatar = _encoded(ImageOps.fit(crop, (320, 320)))
            draw.rectangle(rect, fill="#172133")
        elif span.is_sensitive or "█" in (span.raw_text or "") or _private(span.raw_text or ""):
            draw.rectangle(rect, fill="#172133")
    for box in boxes:
        draw.rectangle(clipped(box), fill="#172133")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return _encoded(image), avatar


def _pdf(path, doc, avatar_allowed):
    import pymupdf
    from PIL import Image

    pages = []
    avatar = None
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf, 1):
            if index > MAX_PAGES:
                break
            if not page.rect.width or not page.rect.height:
                continue
            zoom = min(2.0, MAX_SIDE/max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            boxes = []
            spans = []
            # Scanned-page spans use image coordinates; text spans use unrotated PDF coordinates.
            for span in doc.spans:
                locator = span.locator
                if locator.get("page") != index or "bbox" not in locator:
                    continue
                bbox = locator["bbox"]
                if locator.get("ocr_dpi"):
                    scale = 72 * zoom / locator["ocr_dpi"]
                    rect = [value*scale for value in bbox]
                elif span.span_kind == "photo":
                    rect = [value*zoom/2 for value in bbox]
                else:
                    rect = [value*zoom for value in (pymupdf.Rect(bbox) * page.rotation_matrix)]
                spans.append((span, rect))
            # Redact individual text lines, including paired table labels and neighboring values.
            for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT).get("blocks", []):
                for line in block.get("lines", []):
                    if _private("".join(item["text"] for item in line["spans"])):
                        boxes.append([x*zoom for x in (pymupdf.Rect(line["bbox"]) * page.rotation_matrix)])
            content, photo = _page_art(image, spans, boxes, avatar_allowed and avatar is None)
            pages.append((index, content))
            avatar = avatar or photo
    return pages, avatar


def render_preview(path, mime, document, avatar_allowed):
    if mime.startswith("image/"):
        from PIL import Image

        with Image.open(path) as image:
            page = image.convert("RGB")
        content, avatar = _page_art(page, [(span, span.locator["bbox"]) for span in document.spans
                                          if "bbox" in span.locator], [], avatar_allowed)
        return [(1, content)], avatar
    if mime == "application/pdf":
        return _pdf(path, document, avatar_allowed)
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        return [], None
    with TemporaryDirectory(prefix="resume-word-preview-") as directory:
        subprocess.run([sys.executable, "-m", "app.parsing.doc_convert", path, directory, "pdf"],
                       timeout=90, check=True, capture_output=True)
        converted = str(Path(directory) / (Path(path).stem + ".pdf"))
        mapped = pipeline.enrich_pdf_with_ocr(pipeline.parse_pdf(converted), converted)
        for page in mapped.pages[:MAX_PAGES]:
            if page.page_no not in mapped.meta.get("ocr_pages", []):
                mapped.spans.extend(pipeline.detect_photo_on_pdf_page(converted, page.page_no))
        return _pdf(converted, mapped, avatar_allowed)
