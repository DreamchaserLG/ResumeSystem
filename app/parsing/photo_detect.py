"""照片（头像）区域检测与脱敏预览（03 §3）。

判定依据（满足任一 → photo）：
  a) 版面先验：figure 位于页面右上/左上角或侧栏顶部，宽高比 0.6–1.7，短边 60–600px
  b) 人脸检测（OpenCV YuNet DNN，cv2 5.x 无 Haar）在 figure 内命中
普通图形（logo/图标/盖章）→ 不产生 photo span，仅版面元数据。
"""
import io
import logging
import os
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.parsing.ir import Span

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "yunet.onnx")
_det_obj = None
_det_unavailable = False
_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _detector():
    global _det_obj, _det_unavailable
    if _det_obj is None and not _det_unavailable:
        with _lock:
            if _det_obj is None and not _det_unavailable:
                try:
                    # OpenCV's Windows path loader cannot reliably open non-ASCII paths.
                    # The in-memory overload reads the bundled model regardless of cwd.
                    _det_obj = cv2.FaceDetectorYN.create(
                        "onnx", Path(MODEL_PATH).read_bytes(), b"", (320, 320), score_threshold=0.6
                    )
                except (OSError, cv2.error):
                    _det_unavailable = True
                    logger.warning("YuNet face detector unavailable; using layout photo detection")
    return _det_obj


def detect_photo_regions(
    image: Image.Image,
    figure_regions: list[list[float]] | None = None,
    text_lines: list[list[float]] | None = None,
    page_no: int = 1,
) -> list[Span]:
    """在整页图中检测照片区域。

    figure_regions：版面分析给出的候选框；缺省时用角部启发式扫描。
    text_lines：OCR 文本行 bbox——照片区域不送 OCR，候选框内文本覆盖过高必为文本块，排除。
    """
    img = image if image.mode == "RGB" else image.convert("RGB")
    W, H = img.size
    results: list[Span] = []

    candidates = figure_regions or []
    for bbox in candidates:
        x0, y0, x1, y1 = [int(v) for v in bbox]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        if text_lines and _text_overlap_ratio((x0, y0, x1, y1), text_lines) > 0.15:
            continue  # 区域内有 OCR 文本 → 文本块，不是照片
        kind = _classify_figure(img, (x0, y0, x1, y1))
        if kind == "photo":
            results.append(
                Span(
                    span_kind="photo",
                    raw_text=None,
                    locator={"page": page_no, "bbox": [x0, y0, x1, y1]},
                    extraction_method="layout_detection",
                    reading_order=10**6 + y0,  # photo 不参与正文阅读顺序，pipeline 最终排除
                )
            )
    if not results and page_no == 1:
        results.extend(Span("photo", None, {"page":page_no,"bbox":box}, "layout_detection",
                            reading_order=10**6+box[1]) for box in _header_portraits(img, text_lines))
    return results


def _text_overlap_ratio(bbox: tuple[int, int, int, int], text_lines: list[list[float]]) -> float:
    """候选框内被 OCR 文本行覆盖的面积比例。"""
    bx0, by0, bx1, by1 = bbox
    box_area = max(1, (bx1 - bx0) * (by1 - by0))
    covered = 0
    for lx0, ly0, lx1, ly1 in text_lines:
        ix0, iy0 = max(bx0, lx0), max(by0, ly0)
        ix1, iy1 = min(bx1, lx1), min(by1, ly1)
        if ix1 > ix0 and iy1 > iy0:
            covered += (ix1 - ix0) * (iy1 - iy0)
    return min(1.0, covered / box_area)


def _classify_figure(img: Image.Image, bbox: tuple[int, int, int, int]) -> str | None:
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w < 40 or h < 40:
        return None  # 太小：图标
    aspect = w / h
    in_size = 60 <= min(w, h) <= 600

    face_hit = _detect_face(img, bbox)

    if face_hit:
        return "photo"
    if in_size and 0.55 <= aspect <= 1.7 and _near_corner_or_top(img, bbox):
        pixels = np.asarray(img.crop(bbox).resize((32, 32)), dtype=np.float32)
        if float(pixels.std(axis=(0, 1)).mean()) >= 9:
            return "photo"
    return "graphic"


def _face_boxes(img: Image.Image, bbox: tuple[int, int, int, int]) -> list[list[float]]:
    """Return face rectangles in page coordinates."""
    crop = np.array(img.crop(bbox))          # RGB
    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if w < 24 or h < 24:
        return []
    det = _detector()
    if det is None:
        return []
    with _lock:
        det.setInputSize((w, h))
        _, faces = det.detect(bgr)
    origin_x, origin_y = bbox[:2]
    return [[origin_x+x, origin_y+y, origin_x+x+width, origin_y+y+height]
            for x,y,width,height in faces[:, :4]] if faces is not None else []


def _detect_face(img: Image.Image, bbox: tuple[int, int, int, int]) -> bool:
    return bool(_face_boxes(img, bbox))


def _header_portraits(img, text_lines):
    width, height = img.size
    # Flattened scans and clipped PDF tiles may have no usable image-object box.
    for x0, x1 in ((0, int(width*.48)), (int(width*.52), width)):
        if x1-x0 < 24 or height*.32 < 24:
            continue
        for left, top, right, bottom in _face_boxes(img, (x0, 0, x1, int(height*.32))):
            side = max(right-left, bottom-top)
            if side < 24:
                continue
            portrait = [max(0, left-.4*side), max(0, top-.55*side),
                        min(width, right+.4*side), min(height, bottom+.65*side)]
            if not text_lines or _text_overlap_ratio(portrait, text_lines) <= .10:
                yield [round(float(value)) for value in portrait]


def _near_corner_or_top(img: Image.Image, bbox: tuple[int, int, int, int]) -> bool:
    W, H = img.size
    x0, y0, x1, _ = bbox
    margin_x = W * 0.35
    top = y0 < H * 0.35
    left = x0 < margin_x
    right = x1 > W - margin_x
    return top and (left or right)


def build_masked_preview(image_bytes: bytes, photo_bboxes: list[list[float]], block: int = 14) -> bytes:
    """生成脱敏预览：仅对 photo 区域像素化，其余内容保持原样（03 §3.2）。"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    for bbox in photo_bboxes:
        x0, y0, x1, y1 = [int(v) for v in bbox]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.width, x1), min(img.height, y1)
        w, h = x1 - x0, y1 - y0
        if w <= 2 or h <= 2:
            continue
        region = img.crop((x0, y0, x1, y1))
        mosaic = (
            region.resize((max(1, w // block), max(1, h // block)))
            .resize((w, h), Image.NEAREST)
        )
        img.paste(mosaic, (x0, y0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
