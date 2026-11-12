"""私有文件存储（03 §7）：本地目录起步，object_key 全程引用，兼容后续 MinIO/S3。"""
import os

from app.core.config import get_settings


def storage_root() -> str:
    root = get_settings().storage_root
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, "originals"), exist_ok=True)
    os.makedirs(os.path.join(root, "previews"), exist_ok=True)
    return root


def save_original(candidate_key: str, filename: str, data: bytes) -> tuple[str, str]:
    """保存原件，返回 (object_key, sha256)。

    object_key 形如 originals/{candidate_key}/{sha[:2]}/{sha}_{filename}。
    """
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")[:80] or "resume.bin"
    key = f"originals/{candidate_key}/{sha[:2]}/{sha}_{safe_name}"
    path = os.path.join(storage_root(), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return key, sha


def save_preview(preview_data: bytes, base_key: str) -> str:
    """保存脱敏预览：previews/{base_key}.png。"""
    key = f"previews/{base_key}.png"
    path = os.path.join(storage_root(), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(preview_data)
    return key


def open_file(object_key: str) -> str:
    """返回本地绝对路径（解析器直接读文件）。"""
    path = os.path.join(storage_root(), object_key)
    if not os.path.exists(path):
        raise FileNotFoundError(object_key)
    return path


def read_file(object_key: str) -> bytes:
    with open(open_file(object_key), "rb") as f:
        return f.read()
