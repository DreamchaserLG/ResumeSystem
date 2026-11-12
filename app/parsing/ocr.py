"""PaddleOCR 封装（03 §2）：懒加载单例，文本行 + 置信度 + 框坐标。

模型权重首次调用时下载到 ~/.paddlex；生产环境预置到镜像或挂载卷。
版面分析（PP-StructureV3 figure 区域）为可选增强：v1 文本路径已验证，
photo 检测默认走版面先验 + 人脸判定（photo_detect），版面区域可注入以提升精度。
"""
import os
import threading

from app.parsing.reading_order import by_columns

_lock = threading.Lock()
_ocr = None

DISABLE_MODEL_SOURCE_CHECK = True  # 跳过 paddle 对外网模型源的连通性检查


def _get_ocr():
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
                from paddleocr import PaddleOCR

                _ocr = PaddleOCR(
                    text_recognition_model_name="PP-OCRv5_server_rec",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
    return _ocr


def ocr_page(image_path: str) -> dict:
    """识别单页：返回 {"lines": [{"text","score","bbox"}], "page_w","page_h"}。"""
    r = _get_ocr().predict(image_path)[0]
    texts = r["rec_texts"]
    scores = r["rec_scores"]
    polys = r["rec_polys"]
    lines = []
    for text, score, poly in zip(texts, scores, polys):
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        lines.append(
            {
                "text": str(text),
                "score": float(score),
                "bbox": [
                    round(min(xs), 1),
                    round(min(ys), 1),
                    round(max(xs), 1),
                    round(max(ys), 1),
                ],
            }
        )
    # 阅读顺序：按 y 主序、x 副序（多栏恢复 v1 为线性排序，双栏简历单独评估项）
    img = _peek_image_size(image_path)
    lines = by_columns(lines, img[0])
    return {"lines": lines, "page_w": img[0], "page_h": img[1]}


def _peek_image_size(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


# ========= 版面分析（PP-DocLayout）：figure/image 区域 → 照片检测候选 =========
_layout_model = None
_layout_lock = threading.Lock()


def get_layout_regions(image_path: str) -> list[dict]:
    """版面区域：[{label, score, bbox}]。image/figure 标签为照片检测候选。

    独立模型（PP-DocLayout_plus-L），不引入 PPStructureV3 全家桶。
    首次调用下载权重至 ~/.paddlex。
    """
    global _layout_model
    if _layout_model is None:
        with _layout_lock:
            if _layout_model is None:
                os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
                from paddlex import create_model

                _layout_model = create_model("PP-DocLayout_plus-L")
    results = list(_layout_model.predict(image_path))
    r0 = results[0]
    boxes = r0["boxes"] if isinstance(r0, dict) and "boxes" in r0 else getattr(r0, "boxes", [])
    out = []
    for b in boxes or []:
        bb = b.get("bbox", b.get("coordinate", []))
        out.append(
            {
                "label": b.get("label", "unknown"),
                "score": float(b.get("score", 0)),
                "bbox": [float(v) for v in bb],
            }
        )
    return out


def figure_candidates(image_path: str) -> list[list[float]]:
    """版面 image 区域候选框（照片检测输入）。"""
    return [r["bbox"] for r in get_layout_regions(image_path) if r["label"] in ("image", "figure")]
