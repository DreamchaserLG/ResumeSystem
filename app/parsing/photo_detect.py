"""照片（头像）区域检测与脱敏预览（03 §3）。

判定依据（满足任一 → photo）：
  a) 版面先验：figure 位于页面右上/左上角或侧栏顶部，宽高比 0.6–1.7，短边 60–600px
  b) 人脸检测（OpenCV YuNet DNN，cv2 5.x 无 Haar）在 figure 内命中
普通图形（logo/图标/盖章）→ 不产生 photo span，仅版面元数据。
"""
import io
import os
import threading

import cv2
import numpy as np
from PIL import Image

from app.parsing.ir import Span

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "yunet.onnx")
_det_obj = None
_lock = threading.Lock()


def _detector():
    global _det_obj
    if _det_obj is None:
        with _lock:
            if _det_obj is None:
                _det_obj = cv2.FaceDetectorYN.create(
                    MODEL_PATH, "", (320, 320), score_threshold=0.6
                )
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

    candidates = figure_regions if figure_regions else _heuristic_regions(img)
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


def _detect_face(img: Image.Image, bbox: tuple[int, int, int, int]) -> bool:
    """YuNet DNN 人脸检测（cv2 5.x）；输入为候选区域，区域内任一命中即 True。"""
    crop = np.array(img.crop(bbox))          # RGB
    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if w < 24 or h < 24:
        return False
    det = _detector()
    det.setInputSize((w, h))
    _, faces = det.detect(bgr)
    return faces is not None and len(faces) > 0


def _near_corner_or_top(img: Image.Image, bbox: tuple[int, int, int, int]) -> bool:
    W, H = img.size
    x0, y0, x1, _ = bbox
    margin_x = W * 0.35
    top = y0 < H * 0.35
    left = x0 < margin_x
    right = x1 > W - margin_x
    return top and (left or right)


def _heuristic_regions(img: Image.Image) -> list[list[float]]:
    """无版面分析时的粗筛：角部/顶部区块扫描（性能兜底，不作为主路径）。"""
    W, H = img.size
    out = []
    s = int(min(W, H) * 0.18)
    for (x0, y0) in [
        (W - s - 20, 20),        # 右上
        (20, 20),                # 左上
    ]:
        out.append([float(x0), float(y0), float(x0 + s), float(y0 + s)])
    return out


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
