"""Refit section labels in the two generated PDF sets and refresh stored previews."""

import argparse
from collections import Counter
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf
from PIL import Image
from sqlalchemy import text

from app.core.db import owner_engine
from app.core.storage import storage_root
from app.parsing import preview
from app.parsing.heading_bands import fit_heading_bands
from app.parsing.pipeline import parse_document

MIME = "application/pdf"


def signature(pdf):
    return tuple((tuple(page.rect), tuple(page.get_text("blocks", flags=pymupdf.TEXTFLAGS_TEXT))) for page in pdf)


def refit(content, template):
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        before = signature(pdf)
        headings, changed = fit_heading_bands(pdf, template)
        if not changed:
            return content, headings, 0
        result = pdf.tobytes(garbage=3, deflate=True, no_new_id=True)
    with pymupdf.open(stream=result, filetype="pdf") as pdf:
        if signature(pdf) != before or fit_heading_bands(pdf, template) != (headings, 0):
            raise ValueError("heading change altered text geometry or did not pass layout readback")
    return result, headings, changed


def atomic_file(path, content):
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


def digest(content):
    return hashlib.sha256(content).hexdigest()


def refit_archive(folder, template, fixed):
    stats = Counter()
    for path in sorted(folder.rglob("*.pdf")):
        content = path.read_bytes()
        result, count, changed = refit(content, template)
        if changed:
            atomic_file(path, result)
            fixed[digest(content)] = (path, count)
        stats.update(files=1, changed=bool(changed), headings=count)
        if stats["files"] % 250 == 0:
            print(f"archive {template}: {stats['files']} PDFs processed", flush=True)
    print(f"archive {template}: {dict(stats)}", flush=True)


def repack(folder):
    archive = folder.with_suffix(".zip")
    if not archive.exists():
        return
    with ZipFile(archive) as current:
        names = current.namelist()
    updated = archive.with_suffix(".zip.tmp")
    try:
        with ZipFile(updated, "w", ZIP_DEFLATED, compresslevel=3) as out:
            for name in names:
                path = (folder.parent / name).resolve()
                if not path.is_relative_to(folder.resolve()):
                    raise ValueError("unexpected archive entry")
                out.write(path, name)
        with ZipFile(updated) as check:
            if check.testzip() is not None or check.namelist() != names:
                raise ValueError("rebuilt ZIP failed its readback")
        updated.replace(archive)
    finally:
        updated.unlink(missing_ok=True)


def refresh_document(root, doc, fixed):
    original = root / doc["object_key"]
    if not original.is_file() or not original.resolve().is_relative_to(root):
        raise ValueError("stored PDF is missing")
    if not doc["template"]:
        with pymupdf.open(original) as pdf:
            signature(pdf)
        return 0
    old_bytes = original.read_bytes()
    if digest(old_bytes) != doc["sha256"]:
        raise ValueError("stored PDF checksum mismatch")
    cached = fixed.get(doc["sha256"])
    if cached:
        result = cached[0].read_bytes()
    else:
        result, _, changed = refit(old_bytes, doc["template"])
        if not changed:
            return 0
    sha = digest(result)
    output = original.with_name(f"heading-v2_{sha}.pdf")
    atomic_file(output, result)
    key = str(output.relative_to(root))
    parsed = parse_document(str(output), MIME)
    if parsed.meta["has_photo"] or parsed.meta["ocr_pages"]:
        raise ValueError("generated PDF unexpectedly needs photo handling")
    with owner_engine.connect() as connection:
        versions = connection.execute(text("""
            SELECT v.id,v.preview_key,
                   (SELECT count(*) FROM resume_preview_page p WHERE p.version_id=v.id) AS page_count,
                   EXISTS(SELECT 1 FROM resume_preview_page p WHERE p.version_id=v.id AND p.photo_object_key IS NOT NULL) AS has_photo_page
            FROM resume_version v WHERE v.document_id=:id AND v.status='published'
        """), {"id": doc["id"]}).mappings().all()
    sizes = {}
    for version in versions:
        if version["has_photo_page"] or not version["preview_key"]:
            raise ValueError("generated PDF has unsupported existing preview")
        with Image.open(root / version["preview_key"]) as image:
            sizes[version["id"]] = max(image.size)
    variants = {}
    updated = []
    for version in versions:
        size = sizes[version["id"]]
        if size not in variants:
            preview.MAX_SIDE = size
            variants[size], _ = preview.render_preview(str(output), MIME, parsed, False)
        pages = variants[size]
        if not pages or len(pages) != version["page_count"] or any(photo for _, _, photo in pages):
            raise ValueError("preview page count or masking state changed")
        for page, blob, _ in pages:
            preview_key = f"previews/resume-version-{version['id']}-heading-v2-{sha[:12]}-{page}.png"
            atomic_file(root / preview_key, blob)
            updated.append({"id": version["id"], "page": page, "key": preview_key})
    with owner_engine.begin() as connection:
        changed = connection.execute(text("""
            UPDATE resume_document SET object_key=:key,sha256=:sha,file_size=:size
            WHERE id=:id AND object_key=:old AND sha256=:old_sha
        """), {"id": doc["id"], "old": doc["object_key"], "old_sha": doc["sha256"],
                  "key": key, "sha": sha, "size": len(result)})
        if changed.rowcount != 1:
            raise ValueError("stored PDF changed while preparing layout")
        if updated:
            pages = connection.execute(text("""
                UPDATE resume_preview_page SET object_key=:key WHERE version_id=:id AND page_no=:page
            """), updated)
            if pages.rowcount != len(updated):
                raise ValueError("preview pages changed while preparing layout")
            connection.execute(text("UPDATE resume_version SET preview_key=:key WHERE id=:id"),
                               [row for row in updated if row["page"] == 1])
    return len(updated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    fixed = {}
    refit_archive(args.archive, "rebuild", fixed)
    refit_archive(args.legacy, "legacy", fixed)
    repack(args.archive)
    root = Path(storage_root()).resolve()
    with owner_engine.connect() as connection:
        docs = connection.execute(text("""
            SELECT d.id,d.object_key,d.sha256,CASE WHEN EXISTS(SELECT 1 FROM resume_version v
                WHERE v.document_id=d.id AND v.parser_version='pdf-text-v1-source-rebuilt') THEN 'rebuild'
                WHEN d.object_key LIKE 'originals/reconstructed-%' THEN 'legacy' END AS template
            FROM resume_document d WHERE d.mime_type='application/pdf' ORDER BY d.id
        """)).mappings().all()
    stats = Counter()
    for doc in docs:
        try:
            pages = refresh_document(root, doc, fixed)
            stats.update(checked=1, updated=bool(pages), pages=pages)
        except (OSError, RuntimeError, ValueError) as error:
            stats["failed"] += 1
            print(f"stored PDF {doc['id']} layout check failed: {error}", flush=True)
        if (stats['checked'] + stats['failed']) % 100 == 0:
            print(f"stored PDF progress: {dict(stats)}", flush=True)
    print(f"stored PDFs: {dict(stats)}", flush=True)
    if stats["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
