#!/usr/bin/env python3
"""M3 评估（03 §3.3 / 08 §1）：resuparse 抽样分层报告。

口径：
- 解析成功率：pipeline 无异常比例
- 关键字段可读性：姓名（ground truth basics.name）出现在 OCR 文本中（前 60 字符内匹配）
- 照片区域：DocLayout image 区域 × YuNet 人脸 × 文本反证 → photo span 判定
- 分层：ground truth image 字段非空（弱标签，渲染主题可能不画头像）
"""
import json
import os
import random
from pathlib import Path

DATA = Path(os.environ.get("RESUME_OCR_EVAL_DATA", Path(__file__).resolve().parents[2] / "data" / "resuparse"))
N_PER_GROUP = 10

random.seed(7)


def sample_metadata():
    groups = {"with_image": [], "without_image": []}
    for split in ["train", "validation"]:
        with open(f"{DATA}/{split}/metadata.jsonl") as f:
            for line in f:
                d = json.loads(line)
                gt = json.loads(d["ground_truth"])["gt_parse"]
                img = bool(gt.get("basics", {}).get("image"))
                name = gt.get("basics", {}).get("name", "")
                groups["with_image" if img else "without_image"].append(
                    {"split": split, "file": d["file_name"], "name": name}
                )
    return groups


def main():
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    from app.parsing import pipeline
    from app.parsing.ocr import get_layout_regions

    groups = sample_metadata()
    report = {}
    for gname, items in groups.items():
        sample = random.sample(items, min(N_PER_GROUP, len(items)))
        ok = 0
        name_found = 0
        photo_detected = 0
        layout_image_regions = 0
        errors = []
        for it in sample:
            path = f"{DATA}/{it['split']}/{it['file']}"
            try:
                pd = pipeline.parse_document(path, "image/png")
                ok += 1
                texts = " ".join(s.raw_text or "" for s in pd.spans if s.raw_text)
                if it["name"] and it["name"].split()[0][:6].lower() in texts.lower():
                    name_found += 1
                regions = get_layout_regions(path)
                imgs = [r for r in regions if r["label"] in ("image", "figure")]
                layout_image_regions += bool(imgs)
                photo_detected += bool(pd.meta.get("has_photo"))
            except Exception as e:  # noqa: BLE001
                errors.append(type(e).__name__)
        n = len(sample)
        report[gname] = {
            "n": n,
            "parse_ok": ok,
            "parse_rate": round(ok / n, 3),
            "name_readable": name_found,
            "name_rate": round(name_found / n, 3),
            "layout_image_regions": layout_image_regions,
            "photo_detected_final": photo_detected,
        }
        if errors:
            report[gname]["errors"] = errors[:5]

    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = Path(__file__).with_name("m3_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("saved ->", out)


if __name__ == "__main__":
    main()
