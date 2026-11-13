"""Render authorized, layout-preserving previews without exporting the original file."""
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from app.core.extraction import PRIVATE_LABELS, SENSITIVE, norm
from app.parsing import pipeline

MAX_PAGES = 40
MAX_SIDE = 1800
NEXT_FIELD = re.compile(
    r"\s*(?:[★◆♦▪●]|\t| {2,})\s*(?=(?:求职意向|应聘岗位|到岗时间|年龄|籍贯|学历|姓名|"
    r"education|name|objective)\s*[:：])", re.I,
)


def sensitive_ranges(raw):
    start = 0
    while match := SENSITIVE.search(raw, start):
        tail = raw[match.start():match.end()]
        next_field = NEXT_FIELD.search(tail)
        end = match.start() + next_field.start() if next_field else match.end()
        yield match.start(), end
        start = end


def _private(text):
    return bool(SENSITIVE.search(text)) or norm(text.rstrip("：: ")) in PRIVATE_LABELS | {
        "phone": "", "mobile": "", "联系电话": "", "手机": "", "电话": "", "email": "", "邮箱": "",
        "婚姻状况": "", "婚育情况": "", "身份证号": "",
    }


def _encoded(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _page_art(image, photos, boxes, avatar_allowed):
    from PIL import ImageDraw, ImageOps

    def clipped(box):
        x0, y0, x1, y1 = box
        return max(0, int(x0)-1), max(0, int(y0)-1), min(image.width-1, int(x1)+2), min(image.height-1, int(y1)+2)

    private_boxes = [clipped(box) for box in boxes]
    draw = ImageDraw.Draw(image)
    for rect in private_boxes:
        # Paint an opaque, neutral patch. Never blur recoverable contact information.
        draw.rectangle(rect, fill="#edf1f5")
    avatar = None
    visible_photos = None
    if photos:
        if avatar_allowed:
            visible_photos = image.copy()
        for box in photos:
            rect = clipped(box)
            x0,y0,x1,y1 = rect
            safe = (x1-x0 >= 30 and y1-y0 >= 30 and (x1-x0)*(y1-y0) <= image.width*image.height*.25
                    and not any(min(x1,b[2]) > max(x0,b[0]) and min(y1,b[3]) > max(y0,b[1]) for b in private_boxes))
            if avatar_allowed and safe and avatar is None:
                avatar = _encoded(ImageOps.fit(image.crop(rect), (320, 320)))
            if visible_photos is not None and not safe:
                ImageDraw.Draw(visible_photos).rectangle(rect, fill="#edf1f5")
            draw.rectangle(rect, fill="#edf1f5")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    if visible_photos is not None and avatar is not None:
        visible_photos.thumbnail((MAX_SIDE, MAX_SIDE))
    return _encoded(image), avatar, _encoded(visible_photos) if visible_photos is not None and avatar is not None else None


def _span_boxes(spans, scale):
    photos, boxes = [], []
    for span in spans:
        if "bbox" not in span.locator:
            continue
        rect = [value*scale(span) for value in span.locator["bbox"]]
        if span.span_kind == "photo":
            photos.append(rect)
        elif span.is_sensitive or "█" in (span.raw_text or "") or _private(span.raw_text or ""):
            # OCR returns line geometry. Avoid guessing character coordinates on a scan.
            boxes.append(rect)
    return photos, boxes


def _pdf_text_boxes(page, scale):
    import pymupdf

    boxes = []
    for block in page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_TEXT).get("blocks", []):
        for line in block.get("lines", []):
            chars = [char for part in line["spans"] for char in part["chars"]]
            # Match across differently styled spans without masking unrelated parts of the line.
            raw = "".join(char["c"] for char in chars)
            ranges = list(sensitive_ranges(raw))
            if not ranges and _private(raw):
                ranges = [(0,len(raw))]
            if any(len(char["c"]) != 1 for char in chars):
                if ranges:
                    boxes.append(scale(line["bbox"]))
                continue
            for start,end in ranges:
                rect = pymupdf.Rect()
                for char in chars[start:end]:
                    box = pymupdf.Rect(char["bbox"])
                    if (not rect.is_empty and abs(line["dir"][0]) >= .9
                            and (box.x0 > rect.x1 + 1.6 * max(box.height, rect.height)
                                 or box.x1 < rect.x0 - 1.6 * max(box.height, rect.height))):
                        boxes.append(scale(rect))
                        rect = pymupdf.Rect()
                    rect |= box
                if not rect.is_empty:
                    boxes.append(scale(rect))
    return boxes


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
            spans = [span for span in doc.spans if span.locator.get("page") == index
                     and (span.extraction_method == "ocr" or span.span_kind == "photo")]
            photos, boxes = _span_boxes(spans, lambda s: 72*zoom/s.locator["ocr_dpi"] if s.locator.get("ocr_dpi") else zoom/2)
            def scale(rect):
                return [x*zoom for x in (pymupdf.Rect(rect) * page.rotation_matrix)]
            boxes.extend(_pdf_text_boxes(page, scale))
            content, photo, photo_page = _page_art(image, photos, boxes, avatar_allowed)
            pages.append((index, content, photo_page))
            avatar = avatar or photo
    return pages, avatar


def render_preview(path, mime, document, avatar_allowed):
    if mime.startswith("image/"):
        from PIL import Image

        with Image.open(path) as image:
            page = image.convert("RGB")
        photos, boxes = _span_boxes(document.spans, lambda _: 1)
        content, avatar, photo_page = _page_art(page, photos, boxes, avatar_allowed)
        return [(1, content, photo_page)], avatar
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
