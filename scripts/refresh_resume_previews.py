"""Re-render stored resume previews after the contact-masking fix.

The old sensitive-field pattern consumed everything up to the end of the line, so a
header such as "电话：… 邮箱：… 工作年限：20 年" was covered by one light band.  This
script re-renders every stored PDF with the corrected masking, checks that no grey
band spans a line, and points the database at the new images.

Run from resume-system/:
    STORAGE_ROOT=../data/招聘简历文件-20261124 .venv/bin/python -m scripts.refresh_resume_previews
"""
import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from PIL import Image
from sqlalchemy import text

from app.core.db import owner_engine
from app.core.storage import storage_root
from app.parsing import preview
from app.parsing.pipeline import parse_document

MIME = "application/pdf"
MASK = (237, 241, 245)


def atomic_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def mask_widths(image: Image.Image) -> int:
    raster = np.asarray(image.convert("RGB"), dtype=np.int16)
    grey = (np.abs(raster - np.array(MASK, dtype=np.int16)) <= 1).all(axis=2)
    widest = 0
    for row in grey:
        if not row.any():
            continue
        padded = np.concatenate(([False], row, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        if edges.size:
            widest = max(widest, int((edges[1::2] - edges[::2]).max()))
    return widest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--report", type=Path, default=Path(".runtime/preview-refresh.json"))
    parser.add_argument("--apply", action="store_true", help="write images and update the database")
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(storage_root()).resolve()
    with owner_engine.connect() as connection:
        docs = connection.execute(text("""
            SELECT d.id,d.object_key,d.mime_type,v.id AS version_id,
                   (SELECT count(*) FROM resume_preview_page p WHERE p.version_id=v.id) AS pages,
                   (SELECT max(p.object_key) FROM resume_preview_page p WHERE p.version_id=v.id AND p.page_no=1) AS first_key
            FROM resume_document d JOIN resume_version v ON v.document_id=d.id
            WHERE d.mime_type=:mime ORDER BY d.id
        """), {"mime": MIME}).mappings().all()
    old_keys = {}
    with owner_engine.connect() as connection:
        for row in connection.execute(text("""
            SELECT version_id,page_no,object_key FROM resume_preview_page
            WHERE version_id IN (SELECT v.id FROM resume_version v JOIN resume_document d ON d.id=v.document_id
                                 WHERE d.mime_type=:mime)
        """), {"mime": MIME}).mappings():
            old_keys[(row["version_id"], row["page_no"])] = row["object_key"]
    docs = docs[args.offset:]
    if args.limit:
        docs = docs[:args.limit]
    stats = Counter()
    changed = []
    for index, doc in enumerate(docs, 1):
        stats["checked"] += 1
        path = root / doc["object_key"]
        if not doc["pages"] or not path.is_file():
            stats["missing"] += 1
            continue
        with Image.open(root / doc["first_key"]) as first:
            preview.MAX_SIDE = max(first.size)
        parsed = parse_document(str(path), MIME)
        if parsed.meta["has_photo"] or parsed.meta["ocr_pages"]:
            stats["needs_photo_handling"] += 1
            continue
        pages, _ = preview.render_preview(str(path), MIME, parsed, False)
        if len(pages) != doc["pages"]:
            stats["page_count_changed"] += 1
            print(f"{doc['id']}: page count {doc['pages']} -> {len(pages)}", flush=True)
            continue
        rows, widest = [], 0
        for page_no, content, _ in pages:
            image = Image.open(io.BytesIO(content))
            widest = max(widest, mask_widths(image))
            key = f"previews/resume-version-{doc['version_id']}-{page_no}-mask2.png"
            rows.append({"page": page_no, "key": key, "content": content})
        if widest > 0.45 * preview.MAX_SIDE:
            stats["wide_band_left"] += 1
            print(f"{doc['id']}: mask run {widest}px still too wide", flush=True)
            continue
        stats["rendered"] += 1
        changed.append({"id": doc["id"], "version_id": doc["version_id"],
                        "object_key": doc["object_key"], "rows": rows,
                        "old": [old_keys.get((doc["version_id"], item["page"])) for item in rows],
                        "widest_mask": widest})
        if index % 100 == 0:
            print(f"progress {index}/{len(docs)} {dict(stats)}", flush=True)
    widest_overall = max((row["widest_mask"] for row in changed), default=0)
    print(f"rendered {len(changed)} previews, widest mask patch {widest_overall}px, {dict(stats)}", flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(
        [{**{k: v for k, v in row.items() if k != "rows"},
          "rows": [{"page": r["page"], "key": r["key"]} for r in row["rows"]]} for row in changed],
        ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.apply:
        print("dry run: no files written", flush=True)
        return
    for row in changed:
        for item in row["rows"]:
            atomic_file(root / item["key"], item["content"])
    with owner_engine.begin() as connection:
        for row in changed:
            pages = connection.execute(text("""
                UPDATE resume_preview_page SET object_key=:key
                WHERE version_id=:v AND page_no=:page
            """), [{"v": row["version_id"], "page": item["page"], "key": item["key"]} for item in row["rows"]])
            if pages.rowcount != len(row["rows"]):
                raise ValueError(f"preview rows changed for version {row['version_id']}")
            connection.execute(text("UPDATE resume_version SET preview_key=:key WHERE id=:v"),
                               {"v": row["version_id"], "key": row["rows"][0]["key"]})
    print(f"applied {len(changed)} previews", flush=True)


if __name__ == "__main__":
    main()
