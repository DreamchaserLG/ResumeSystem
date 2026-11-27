"""将已经按岗位归类的简历迁入 FTP。仅输出批次计数。"""
import argparse
import json
from ftplib import Error
from pathlib import Path

from app.core import ftp_source as ftp


def ensure_directory(source, path):
    parent, _, name = path.rpartition("/")
    entries = source.list(parent)
    existing = next((entry for entry in entries if entry.name.casefold() == name.casefold()), None)
    if existing is None:
        source.mkdir(path)
    elif not existing.directory or existing.name != name:
        raise ftp.CatalogError("FTP 目录存在名称冲突")


def ensure_marker(source, path, name):
    if not any(entry.name == name and not entry.directory for entry in source.list(path)):
        source.marker(ftp.join(path, name))


def transfer(source, data_root, mapping):
    counts = {"uploaded": 0, "unchanged": 0, "unassigned": 0}
    ensure_marker(source, "", ftp.ROOT_MARKER)
    ensure_directory(source, "2026入职")
    ensure_directory(source, "招聘简历")
    folders = {folder.name: folder for folder in data_root.iterdir() if folder.is_dir() and not folder.is_symlink()}
    for title, local in sorted(folders.items()):
        if not ftp.valid_name(title) or title.startswith("."):
            raise ftp.CatalogError("本地目录包含 Windows 不支持的名称")
        department = mapping.get(title)
        if department is not None and (type(department) is not int or not 0 < department < 10**18):
            raise ftp.CatalogError("岗位对应的部门编号无效")
        group = ftp.join("招聘简历", f"部门-{department}" if department else "待归类")
        ensure_directory(source, group)
        target = ftp.join(group, title)
        ensure_directory(source, target)
        if department:
            ensure_marker(source, target, f"{ftp.JOB_MARKER}{department}")
        entries = {entry.name: entry for entry in source.list(target)}
        for path in sorted(local.iterdir()):
            if path.is_symlink() or not path.is_file() or not ftp.file_mime(path.name):
                continue
            if not ftp.valid_name(path.name) or path.name.startswith("."):
                raise ftp.CatalogError("本地简历包含 Windows 不支持的文件名")
            size = path.stat().st_size
            if not 0 < size <= ftp.MAX_FILE:
                continue
            data = path.read_bytes()
            source.upload(target, path.name, data, existing=entries.get(path.name), listed=True)
            counts["unchanged" if path.name in entries else "uploaded"] += 1
            counts["unassigned"] += int(not department)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="已按岗位归类的本地文件夹")
    parser.add_argument("department_map", type=Path, help="岗位目录名到目标数据库部门编号的 JSON 对象")
    args = parser.parse_args()
    try:
        mapping = json.loads(args.department_map.read_text())
        if not isinstance(mapping, dict) or not args.source.is_dir():
            raise ftp.CatalogError("源目录或部门映射无效")
        with ftp.open_source() as source:
            counts = transfer(source, args.source, mapping)
        print(json.dumps(counts, ensure_ascii=False))
    except ftp.CatalogError as error:
        parser.exit(1, f"{error}\n")
    except (Error, OSError, EOFError, ValueError):
        parser.exit(1, "FTP 迁入失败，请检查源目录、连接和权限。已上传文件保留，可重新执行。\n")


if __name__ == "__main__":
    main()
