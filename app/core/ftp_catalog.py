"""FTP 目录投影、增量简历入库与目录创建。"""
import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from app.api.uploads import validate_upload
from app.core import ftp_source as ftp
from app.core.db import owner_engine
from app.core.recruitment import DEFAULT_ROUNDS, RoundInput, save_rounds
from app.core.resume_pipeline import bump_search
from app.core.storage import open_file, save_original
from app.core.workflow import audit, fresh_departments

LOCK = 2316748309
DEPARTMENT_MARKER = re.compile(r"\.resume-job-([1-9][0-9]{0,17})", re.I)


@dataclass
class Folder:
    path: str
    department: int | None
    job: bool
    files: list[ftp.Entry]


def lock_catalog(connection):
    return connection.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK}).scalar_one()


def departments(connection):
    return {row.id: row.name for row in connection.execute(text(
        "SELECT id,name FROM department WHERE status='active'"
    ))}


def department_names(active):
    names = {name.casefold(): key for key, name in active.items()}
    names.update({f"部门-{key}": key for key in active})
    return names


def snapshot(source, active, previous):
    names = department_names(active)
    folders = []

    def scan(path, inherited, depth):
        if depth > ftp.MAX_DEPTH or len(path.encode()) > 1000:
            raise ftp.CatalogError("FTP 目录层级或路径超出限制")
        entries = source.list(path)
        if not path:
            ftp.check_root(entries)
        children = [entry.name for entry in entries if entry.directory and not entry.name.startswith(".")]
        files = [entry for entry in entries if not entry.directory
                 and not entry.name.startswith(".") and ftp.file_mime(entry.name)]
        markers = [None if entry.directory else DEPARTMENT_MARKER.fullmatch(entry.name)
                   for entry in entries if entry.name.lower().startswith(ftp.JOB_MARKER)]
        if markers:
            if len(markers) != 1 or not markers[0] or depth < 2 or children:
                raise ftp.CatalogError("带部门标记的岗位必须是子目录中的叶子节点")
            department = int(markers[0][1])
            if department not in active or inherited not in {None, department}:
                raise ftp.CatalogError("FTP 岗位的部门标记无效或与上级部门不符")
            folders.append(Folder(path, department, True, files))
            return
        if path:
            name = path.rsplit("/", 1)[-1]
            old = previous.get(path)
            old_dept = old["department_id"] if old and old["job_id"] and old["department_id"] in active else None
            department = inherited or names.get(name.casefold())
            candidate = inherited or old_dept
            is_job = bool(depth >= 2 and not children and candidate and (files or old_dept))
            folders.append(Folder(path, candidate if is_job else department, is_job, files if is_job else []))
            inherited = department
        for name in children:
            scan(ftp.join(path, name), inherited, depth + 1)

    scan("", None, 0)
    return folders


def _write_folder(connection, folder, previous):
    data = {"path": folder.path, "department": folder.department, "job": None, "managed": False}
    if folder.job:
        if previous and previous["job_id"]:
            if previous["department_id"] != folder.department:
                raise ftp.CatalogError("已有岗位的 FTP 目录不能更换部门")
            data.update(job=previous["job_id"], managed=previous["managed_job"])
            if not previous["present"] and data["managed"]:
                connection.execute(text("UPDATE job SET hidden=false,updated_at=now() WHERE id=:job"), data)
        else:
            data["title"] = folder.path.rsplit("/", 1)[-1]
            if len(data["title"]) > 120:
                raise ftp.CatalogError("FTP 岗位名称不能超过 120 个字符")
            matches = connection.execute(text("""
                SELECT j.id FROM job j WHERE j.department_id=:department AND NOT j.hidden
                  AND lower(j.title)=lower(:title)
                  AND NOT EXISTS (SELECT 1 FROM ftp_catalog_folder f WHERE f.job_id=j.id)
                ORDER BY j.id LIMIT 2 FOR UPDATE OF j
            """), data).scalars().all()
            if len(matches) == 1:
                data["job"] = matches[0]
            else:
                data["job"] = connection.execute(text("""
                    INSERT INTO job(department_id,title,status) VALUES (:department,:title,'paused') RETURNING id
                """), data).scalar_one()
                data["managed"] = True
                save_rounds(connection, data["job"], [RoundInput(name=name) for name in DEFAULT_ROUNDS])
    elif previous and previous["job_id"]:
        raise ftp.CatalogError("已有岗位目录不能新增下级目录或移除部门归属")
    if previous and previous["present"] and all(previous[key] == data[value] for key, value in (
        ("department_id", "department"), ("job_id", "job"), ("managed_job", "managed"),
    )):
        return data["job"], False
    connection.execute(text("""
        INSERT INTO ftp_catalog_folder(path,department_id,job_id,managed_job)
        VALUES (:path,:department,:job,:managed)
        ON CONFLICT(path) DO UPDATE SET department_id=excluded.department_id,job_id=excluded.job_id,
            managed_job=excluded.managed_job,present=true
    """), data)
    return data["job"], True


def _application(connection, params, previous, data, created_objects):
    duplicate = connection.execute(text("""
        SELECT a.id,v.id AS version,EXISTS (SELECT 1 FROM ftp_catalog_file f
                                          WHERE f.application_id=a.id AND f.managed_application) AS managed
        FROM application a JOIN resume_version v ON v.id=coalesce(a.pending_version_id,a.active_version_id,a.submitted_version_id)
        JOIN resume_document d ON d.id=v.document_id
        WHERE a.job_id=:job AND d.sha256=:sha AND a.deleted_at IS NULL
        ORDER BY a.id LIMIT 1 FOR UPDATE OF a
    """), params).mappings().one_or_none()
    if duplicate:
        return duplicate["id"], duplicate["version"], duplicate["managed"]
    owned_id = previous["application_id"] if previous and previous["managed_application"] else None
    application = None
    if owned_id:
        application = connection.execute(text("""
            SELECT a.id,a.candidate_id FROM application a
            WHERE a.id=:id AND a.deleted_at IS NULL AND a.job_id=:job
              AND NOT EXISTS (SELECT 1 FROM ftp_catalog_file f WHERE f.application_id=a.id
                              AND f.present AND f.path<>:path)
            FOR UPDATE OF a
        """), {**params, "id": owned_id}).one_or_none()
    candidate = application.candidate_id if application else connection.execute(
        text("INSERT INTO candidate DEFAULT VALUES RETURNING id")
    ).scalar_one()
    params["candidate"] = candidate
    document = connection.execute(text("""
        SELECT id FROM resume_document WHERE candidate_id=:candidate AND sha256=:sha
    """), params).scalar_one_or_none()
    if document is None:
        key, _ = save_original(uuid4().hex, params["filename"], data)
        created_objects.append(key)
        document = connection.execute(text("""
            INSERT INTO resume_document(candidate_id,object_key,original_filename,mime_type,sha256,file_size)
            VALUES (:candidate,:key,:filename,:mime,:sha,:size) RETURNING id
        """), {**params, "key": key}).scalar_one()
    connection.execute(text("SELECT id FROM resume_document WHERE id=:doc FOR UPDATE"), {"doc": document})
    version = connection.execute(text("""
        INSERT INTO resume_version(document_id,version_no,parser_version)
        SELECT :doc,coalesce(max(version_no),0)+1,'pending'
        FROM resume_version WHERE document_id=:doc RETURNING id
    """), {"doc": document}).scalar_one()
    if application:
        connection.execute(text("""
            UPDATE application SET pending_version_id=:version WHERE id=:id
        """), {"version": version, "id": application.id})
        return application.id, version, True
    application_id = connection.execute(text("""
        INSERT INTO application(job_id,submitted_job_id,candidate_id,submitted_department_id,
                                handling_department_id,submitted_version_id)
        VALUES (:job,:job,:candidate,:department,:department,:version) RETURNING id
    """), {**params, "version": version}).scalar_one()
    return application_id, version, True


def _write_file(connection, source, folder, job_id, entry, previous, created_objects):
    path = ftp.join(folder.path, entry.name)
    if len(path.encode()) > 1200:
        raise ftp.CatalogError("FTP 简历路径超出限制")
    size, modified = source.fingerprint(path, entry)
    if previous and previous["present"] and (previous["file_size"], previous["modified"]) == (size, modified):
        return "unchanged", None
    params = {"path": path, "folder": folder.path, "job": job_id, "department": folder.department,
              "size": size, "modified": modified, "filename": entry.name, "mime": ftp.file_mime(entry.name)}
    data = None
    if 0 < size <= ftp.MAX_FILE:
        data = source.read(path, size, modified)
        try:
            validate_upload(params["mime"], data)
        except HTTPException:
            data = None
    old_id = previous["application_id"] if previous else None
    old_managed = previous["managed_application"] if previous else False
    if data is None:
        params.update(sha=previous["sha256"] if previous else None, application=old_id, managed=old_managed,
                      version=previous["version_id"] if previous else None)
        outcome, stale_id = "skipped", None
    else:
        params["sha"] = hashlib.sha256(data).hexdigest()
        application, version, managed = _application(connection, params, previous, data, created_objects)
        params.update(application=application, version=version, managed=managed)
        outcome = "updated" if previous and previous["present"] else "added"
        stale_id = old_id if old_managed and old_id != application else None
    connection.execute(text("""
        INSERT INTO ftp_catalog_file(path,folder_path,file_size,modified,sha256,application_id,version_id,managed_application)
        VALUES (:path,:folder,:size,:modified,:sha,:application,:version,:managed)
        ON CONFLICT(path) DO UPDATE SET file_size=excluded.file_size,modified=excluded.modified,sha256=excluded.sha256,
            application_id=excluded.application_id,version_id=excluded.version_id,managed_application=excluded.managed_application,
            present=true,parse_queued_at=CASE WHEN ftp_catalog_file.version_id = excluded.version_id
                                              THEN ftp_catalog_file.parse_queued_at END
    """), params)
    return outcome, stale_id


def _apply(connection, source, actor_id, created_objects):
    old_folders = {row["path"]: row for row in connection.execute(text("SELECT * FROM ftp_catalog_folder")).mappings()}
    old_files = {row["path"]: row for row in connection.execute(text("SELECT * FROM ftp_catalog_file")).mappings()}
    folders = snapshot(source, departments(connection), old_folders)
    result = dict(folders=0, added=0, updated=0, deleted=0, skipped=0, unchanged=0)
    seen_folders, seen_files, stale_ids = set(), set(), set()
    for folder in folders:
        seen_folders.add(folder.path)
        job_id, changed = _write_folder(connection, folder, old_folders.get(folder.path))
        result["folders"] += int(changed)
        for entry in folder.files:
            path = ftp.join(folder.path, entry.name)
            seen_files.add(path)
            status, stale = _write_file(connection, source, folder, job_id, entry, old_files.get(path), created_objects)
            result[status] += 1
            if stale:
                stale_ids.add(stale)

    removed_folders = [path for path, row in old_folders.items() if row["present"] and path not in seen_folders]
    removed_files = [path for path, row in old_files.items() if row["present"] and path not in seen_files]
    if removed_folders:
        params = {"paths": removed_folders}
        connection.execute(text("""
            UPDATE job SET hidden=true,updated_at=now() WHERE id IN (
                SELECT job_id FROM ftp_catalog_folder WHERE managed_job AND path=ANY(CAST(:paths AS text[]))
            )
        """), params)
        connection.execute(text("UPDATE ftp_catalog_folder SET present=false WHERE path=ANY(CAST(:paths AS text[]))"), params)
        result["folders"] += len(removed_folders)
    if removed_files:
        ids = connection.execute(text("""
            UPDATE ftp_catalog_file SET present=false WHERE path=ANY(CAST(:paths AS text[]))
            RETURNING CASE WHEN managed_application THEN application_id END
        """), {"paths": removed_files}).scalars()
        stale_ids.update(key for key in ids if key is not None)
        result["deleted"] = len(removed_files)
    if stale_ids:
        connection.execute(text("""
            UPDATE application a SET deleted_at=now(),workflow_version=workflow_version+1
            WHERE a.id=ANY(CAST(:ids AS bigint[])) AND a.deleted_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM ftp_catalog_file f WHERE f.application_id=a.id AND f.present)
        """), {"ids": list(stale_ids)})
    if any(result[key] for key in ("folders", "added", "updated", "deleted")):
        bump_search(connection)
        audit(connection, actor_id, "ftp_catalog_synced", "ftp", result, target_type="ftp_catalog")
    connection.execute(text("""
        INSERT INTO ftp_catalog_state(singleton,synced_at) VALUES (true,now())
        ON CONFLICT(singleton) DO UPDATE SET synced_at=excluded.synced_at
    """))
    return result


def synchronize(actor_id=None):
    created_objects = []
    try:
        with owner_engine.begin() as connection:
            if not lock_catalog(connection):
                return None
            with ftp.open_source() as source:
                return _apply(connection, source, actor_id, created_objects)
    except Exception:
        for key in created_objects:
            with suppress(OSError):
                Path(open_file(key)).unlink()
        raise


def create_folder(parent, name, department, auth):
    if not ftp.valid_name(name) or name.startswith("."):
        raise HTTPException(422, "请输入符合 Windows 规则的文件夹名称")
    path = ftp.join(parent, name)
    if len(path.encode()) > 1000 or path.count("/") >= ftp.MAX_DEPTH:
        raise HTTPException(422, "目录层级或路径超出限制")
    if department and (not parent or len(name) > 120):
        raise HTTPException(422, "岗位必须建在子目录中，名称最多 120 个字符")
    with owner_engine.begin() as connection:
        if not lock_catalog(connection):
            raise HTTPException(409, "目录正在同步，请稍后重试")
        scope = fresh_departments(connection, auth)
        active = departments(connection)
        container = None
        if parent:
            container = connection.execute(text("""
                SELECT department_id,job_id FROM ftp_catalog_folder WHERE path=:path AND present
            """), {"path": parent}).mappings().one_or_none()
            if container is None:
                raise HTTPException(404, "上级目录不存在，请先同步数据")
            if container["job_id"]:
                raise HTTPException(422, "岗位是叶子节点，不能新建子目录")
        inherited = container["department_id"] if container else None
        if auth.role != "super_admin" and inherited not in scope:
            raise HTTPException(403, "只能在负责的部门目录中创建")
        if department and (department not in scope or department not in active or inherited not in {None, department}):
            raise HTTPException(422, "请选择与目录匹配的有效部门")
        with ftp.open_source() as source:
            root_entries = source.list("")
            ftp.check_root(root_entries)
            entries = source.list(parent) if parent else root_entries
            if any(entry.name.lower().startswith(ftp.JOB_MARKER) for entry in entries):
                raise HTTPException(422, "岗位是叶子节点，不能新建子目录")
            if any(entry.name.casefold() == name.casefold() for entry in entries):
                raise HTTPException(409, "该名称已存在，请同步目录或更换名称")
            source.mkdir(path)
            if department:
                try:
                    source.marker(ftp.join(path, f"{ftp.JOB_MARKER}{department}"))
                except Exception:
                    # 标记若已写完只丢失了响应，保留目录以便下次同步识别。
                    with suppress(Exception):
                        source.client.rmd(path)
                    raise
            assigned = department or inherited or department_names(active).get(name.casefold())
            previous = connection.execute(text("SELECT * FROM ftp_catalog_folder WHERE path=:path"),
                                          {"path": path}).mappings().one_or_none()
            job_id, _ = _write_folder(connection, Folder(path, assigned, department is not None, []), previous)
            audit(connection, auth.user_id, "ftp_folder_created", job_id or "folder",
                  {"department_id": assigned}, target_type="ftp_catalog")
            if job_id:
                bump_search(connection)
    return {"path": path, "job_id": job_id}


def upload_resume(job_id, name, request_id, data, auth):
    mime = ftp.file_mime(name)
    if not ftp.valid_name(name) or name.startswith(".") or not mime:
        raise HTTPException(422, "请选择文件名有效的 PDF、Word 或图片简历")
    if not data:
        raise HTTPException(422, "文件为空")
    if len(data) > ftp.MAX_FILE:
        raise HTTPException(413, "单份简历不能超过 20 MB")
    validate_upload(mime, data)
    filename = PurePosixPath(name)
    stored_name = f"{filename.stem[:80]}-{request_id.hex}{filename.suffix}"
    with owner_engine.begin() as connection:
        scope = fresh_departments(connection, auth)
        row = connection.execute(text("""
            SELECT f.path,f.department_id FROM ftp_catalog_folder f
            JOIN job j ON j.id=f.job_id JOIN department d ON d.id=j.department_id
            WHERE f.job_id=:job AND f.present AND NOT j.hidden AND j.status='open'
              AND d.status='active' AND j.department_id=ANY(CAST(:scope AS bigint[]))
        """), {"job": job_id, "scope": scope}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "招聘中的 FTP 岗位不存在或超出当前权限")
        with ftp.open_source() as source:
            ftp.check_root(source.list(""))
            source.upload(row["path"], stored_name, data)
        audit(connection, auth.user_id, "ftp_resume_uploaded", job_id, target_type="job")
