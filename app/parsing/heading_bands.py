"""Fit the blue section bands in generated resume PDFs to their title text."""

import re

import pymupdf


SECTION_LABELS = frozenset({
    "教育经历", "工作经历", "项目经历", "实习经历", "技能", "专业技能", "语言能力",
    "证书与荣誉", "获奖经历", "自我评价", "源库提取正文",
})
BLUE = {"rebuild": (54, 93, 131), "legacy": (42, 82, 129)}
FILL_RECT = re.compile(rb"(?m)^(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+) re\s+f(?=\s|$)")
STROKE_LINE = re.compile(rb"(?m)^(-?[\d.]+) (-?[\d.]+) m\s+(-?[\d.]+) (-?[\d.]+) l"
                         rb"(?:\s+[\d.]+ w)?(?:\s+[\d.]+ [\d.]+ [\d.]+ RG)?\s+S(?=\s|$)")


def _key(rect):
    return tuple(round(value, 2) for value in rect)


def _span_rect(span):
    return pymupdf.Rect(span["bbox"])


def _page_masks(page, template):
    color = BLUE[template]
    headings_min_width = 300 if template == "rebuild" else 65
    drawings = page.get_drawings()
    bands = {}
    masks_already_drawn = []
    dividers = []
    blue_rules = []
    for path in drawings:
        if len(path["items"]) != 1:
            continue
        rect = path["rect"]
        if path["type"] == "s" and path["items"][0][0] == "l" and rect.width > 80 and rect.height < .1:
            stroke = tuple(round(value * 255) for value in path["color"])
            if template == "legacy" and stroke == (167, 182, 202):
                dividers.append(rect)
            elif stroke == color and path["width"] <= .7:
                blue_rules.append((rect, "".join(path["dashes"].split()).startswith("[]")))
        if path["type"] != "f" or path["items"][0][0] != "re":
            continue
        rgb = tuple(round(value * 255) for value in path["fill"])
        if rgb == (255, 255, 255):
            masks_already_drawn.append(rect)
        if rgb == color and rect.width >= headings_min_width and 2 <= rect.height <= 28:
            bands[_key(rect)] = rect

    spans = [span for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]
             for line in block.get("lines", []) for span in line["spans"] if span["text"].strip()]
    masks = []
    padding = set()
    separators = []
    obsolete = set()
    headings = 0
    for band in bands.values():
        if band.height < 9 and template == "rebuild":
            # A wrapped heading can leave two identical pieces of blue padding on the next page.
            padding.add(_key(band))
            continue
        else:
            white = [span for span in spans if span["color"] == 0xFFFFFF
                     and band.x0 <= span["bbox"][0] <= band.x0 + 100
                     and band.y0 <= (span["bbox"][1] + span["bbox"][3]) / 2 <= band.y1]
            white.sort(key=lambda span: span["bbox"][0])
            label = "".join("".join(span["text"].split()) for span in white)
            if label not in SECTION_LABELS:
                raise ValueError(f"Unknown blue section band on page {page.number + 1}")
            title = _span_rect(white[0])
            for span in white[1:]:
                title |= _span_rect(span)
            right = min(band.x1, title.x1 + 5.5)
            top = max(band.y0, title.y0 - 1.5)
            bottom = min(band.y1, title.y1 + 1.5)
            pieces = [pymupdf.Rect(right, band.y0, band.x1, band.y1),
                      pymupdf.Rect(band.x0, band.y0, right, top),
                      pymupdf.Rect(band.x0, bottom, right, band.y1)]
            legacy_rule = next((line for line in dividers
                                if abs(line.x0 - band.x1) < 2 and abs(line.y0 - band.y1) < 1.5), None)
            if legacy_rule:
                obsolete.add(_key(legacy_rule))
            obsolete.update(_key(line) for line, _ in blue_rules
                            if abs(line.x0 - band.x0) < 1 and abs(line.y0 - band.y1) < 1.5)
            # Start inside the lower corner so antialiasing cannot leave a gap.
            end = page.rect.width - 56 if template == "legacy" else band.x1
            rule = pymupdf.Rect(right - .25, bottom - .2, end, bottom - .2)
            aligned = [(line, solid) for line, solid in blue_rules
                       if all(abs(a - b) < .03 for a, b in zip(rule, line, strict=True))]
            obsolete.update(_key(line) for line, solid in aligned if not solid)
            if not any(solid for _, solid in aligned):
                separators.append(rule)
            headings += 1
        for rect in pieces:
            if rect.width >= .25 and rect.height >= .25:
                # Cover the old band's antialiased outer edge at preview resolution.
                bleed = pymupdf.Rect(rect.x0 - (.5 if rect.x0 == band.x0 else 0),
                                     rect.y0 - (.5 if rect.y0 == band.y0 else 0),
                                     rect.x1 + (.5 if rect.x1 == band.x1 else 0),
                                     rect.y1 + (.5 if rect.y1 == band.y1 else 0))
                if not any(bleed.intersects(_span_rect(span)) for span in spans):
                    rect = bleed
            if rect.width < .25 or rect.height < .25 or any(
                all(abs(a - b) < .03 for a, b in zip(rect, previous, strict=True))
                for previous in masks_already_drawn
            ):
                continue
            if any(rect.intersects(_span_rect(span)) for span in spans):
                raise ValueError(f"Section band overlaps text on page {page.number + 1}")
            masks.append(rect)
    return headings, masks, padding, separators, obsolete


def _remove_old_graphics(pdf, page, padding, obsolete):
    removed = set()
    removed_lines = set()

    def clear_rectangle(match):
        x, y, width, height = map(float, match.groups())
        key = _key((x, y, x + width, y + height))
        if key not in padding:
            return match[0]
        removed.add(key)
        return match[0][:-1] + b"n"

    def clear_line(match):
        x0, y0, x1, y1 = map(float, match.groups())
        a = pymupdf.Point(x0, y0) * page.transformation_matrix
        b = pymupdf.Point(x1, y1) * page.transformation_matrix
        candidate = (a.x, a.y, b.x, b.y)
        hit = next((line for line in obsolete if all(abs(a - b) < .04
                    for a, b in zip(candidate, line, strict=True))), None)
        if hit is None:
            return match[0]
        removed_lines.add(hit)
        return match[0][:-1] + b"n"

    # Story writes these rectangles in page coordinates. Remove the paint operator
    # so text on continuation pages remains above the page's white background.
    for xref in page.get_contents():
        stream = pdf.xref_stream(xref)
        cleaned = FILL_RECT.sub(clear_rectangle, stream) if padding else stream
        if obsolete:
            cleaned = STROKE_LINE.sub(clear_line, cleaned)
        if cleaned != stream:
            pdf.update_stream(xref, cleaned)
    if removed != padding or removed_lines != obsolete:
        raise ValueError(f"Cannot identify old heading graphics on page {page.number + 1}")


def fit_heading_bands(pdf, template):
    """Make white-page background changes while keeping the PDF text coordinates intact."""
    plans = [_page_masks(page, template) for page in pdf]
    color = tuple(value / 255 for value in BLUE[template])
    for page, (_, masks, padding, separators, obsolete) in zip(pdf, plans, strict=True):
        if padding or obsolete:
            _remove_old_graphics(pdf, page, padding, obsolete)
        for mask in masks:
            page.draw_rect(mask, color=None, fill=(1, 1, 1))
        for line in separators:
            page.draw_line(line.tl, line.tr, color=color, width=.55)
    return (sum(count for count, _, _, _, _ in plans),
            sum(len(masks) + len(padding) + len(separators) + len(obsolete)
                for _, masks, padding, separators, obsolete in plans))
