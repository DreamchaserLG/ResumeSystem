"""验证岗位目录接口、增量投影与 Windows 目录兼容。"""
from contextlib import contextmanager
from ftplib import error_perm
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import ftp_catalog as api
from app.core import ftp_catalog as catalog, ftp_source as ftp
from app.core.auth import AuthContext, require_auth
from app.core.config import Settings
from app.core.db import owner_engine
from app.main import app


class MemorySource:
    def __init__(self):
        self.folders = {"": {ftp.ROOT_MARKER: b""}}
        self.versions = {}
        self.downloads = []
        self.fail_read = False

    def list(self, path):
        return [ftp.Entry(name, data is None, len(data or b""), self.versions.get(ftp.join(path, name), ""))
                for name, data in self.folders[path].items()]

    def mkdir(self, path):
        parent, _, name = path.rpartition("/")
        self.folders[parent][name] = None
        self.folders[path] = {}

    def marker(self, path):
        parent, _, name = path.rpartition("/")
        self.folders[parent][name] = b""

    def fingerprint(self, path, entry):
        return entry.size, entry.modified

    def read(self, path, size, modified):
        self.downloads.append(path)
        if self.fail_read:
            raise OSError("模拟中断")
        parent, _, name = path.rpartition("/")
        return self.folders[parent][name]

    def upload(self, folder, name, data):
        self.folders[folder][name] = data
        self.versions[ftp.join(folder, name)] = "20260822000000"


def test_windows_listing_fallback_and_filename_rules():
    class IIS:
        def mlsd(self, path, facts):
            raise error_perm("502 Command not implemented")

        def retrlines(self, command, callback):
            callback("08-22-26  03:10PM       <DIR>          2026入职")
            callback("08-22-26  03:10PM                  125 简历 1.pdf")

        def sendcmd(self, command):
            return "213 20260822071000"

    source = ftp.FtpSource(IIS())
    directory, file = source.list("")
    assert directory == ftp.Entry("2026入职", True)
    assert source.fingerprint(file.name, file) == (125, "20260822071000")
    assert ftp.valid_name("技术研发 / Linux") is False
    assert ftp.valid_name("COM1.txt") is False
    assert ftp.valid_name("技术研发") is True


def test_catalog_api_and_incremental_database(monkeypatch, tmp_path):
    source = MemorySource()
    admin = AuthContext(1, "", "", "super_admin", 1, [1, 2])
    hr = AuthContext(2, "", "", "dept_hr", 1, [1])
    actor = [admin]
    namespace = "目录测试-" + uuid4().hex
    role_path = f"{namespace}/部门-1"
    position = f"{role_path}/开发岗位"
    file_path = f"{position}/合成简历.pdf"
    monkeypatch.setattr(ftp, "enabled", lambda settings=None: True)

    @contextmanager
    def open_source():
        yield source

    monkeypatch.setattr(ftp, "open_source", open_source)
    monkeypatch.setattr(api, "get_redis", lambda: None)
    monkeypatch.setattr("app.core.storage.get_settings", lambda: type("Local", (), {"storage_root": str(tmp_path)})())
    monkeypatch.setattr(catalog, "fresh_departments", lambda connection, auth: auth.departments)
    monkeypatch.setattr(api, "fresh_departments", lambda connection, auth: auth.departments)
    monkeypatch.setitem(app.dependency_overrides, require_auth, lambda: actor[0])

    with owner_engine.connect() as connection, connection.begin() as transaction:
        class IsolatedEngine:
            @contextmanager
            def begin(self):
                with connection.begin_nested():
                    yield connection

        monkeypatch.setattr(catalog, "owner_engine", IsolatedEngine())
        monkeypatch.setattr(api, "owner_engine", IsolatedEngine())
        with TestClient(app) as client:
            base = "/api/v1/job-catalog"
            assert client.post(base + "/folders", json={"name": "根目录岗位", "department_id": 1}).status_code == 422
            assert client.post(base + "/folders", json={"name": namespace}).status_code == 201
            assert client.post(base + "/folders", json={"parent": namespace, "name": "部门-1"}).status_code == 201
            actor[0] = hr
            assert client.post(base + "/sync").status_code == 403
            assert client.post(base + "/folders", json={"parent": role_path, "name": "错部门", "department_id": 2}).status_code == 422
            created = client.post(base + "/folders", json={"parent": role_path, "name": "开发岗位", "department_id": 1})
            assert created.status_code == 201, created.json()
            job = created.json()["job_id"]
            assert client.post(base + "/folders", json={"parent": position, "name": "新的子目录"}).status_code == 422
            visible = {row["path"] for row in client.get(base).json()["folders"]}
            assert {namespace, role_path, position} <= visible
            actor[0] = AuthContext(3, "", "", "dept_hr", 1, [2])
            assert position not in {row["path"] for row in client.get(base).json()["folders"]}
            assert client.post(base + f"/jobs/{job}/uploads?request_id={uuid4()}",
                               files={"file": ("合成简历.pdf", b"%PDF-sample")}).status_code == 404
            connection.execute(text("UPDATE job SET status='open' WHERE id=:job"), {"job": job})
            actor[0] = hr
            assert client.post(base + f"/jobs/{job}/uploads?request_id={uuid4()}",
                               files={"file": ("浏览器上传.pdf", b"%PDF-upload")}).status_code == 201

            source.folders[position]["合成简历.pdf"] = b"%PDF-first"
            source.versions[file_path] = "20260822140000"
            first = catalog.synchronize()
            assert first["added"] == 2
            application = connection.execute(text("SELECT application_id FROM ftp_catalog_file WHERE path=:p"),
                                             {"p": file_path}).scalar_one()
            source.downloads.clear()
            assert catalog.synchronize()["unchanged"] >= 2
            assert source.downloads == []
            source.folders[position]["合成简历.pdf"] = b"%PDF-second"
            source.versions[file_path] = "20260822140100"
            source.fail_read = True
            try:
                catalog.synchronize()
            except OSError:
                pass
            else:
                raise AssertionError("FTP 下载失败应中止同步")
            assert connection.execute(text("SELECT modified FROM ftp_catalog_file WHERE path=:p"),
                                      {"p": file_path}).scalar_one() == "20260822140000"
            source.fail_read = False
            assert catalog.synchronize()["updated"] == 1
            assert connection.execute(text("SELECT pending_version_id FROM application WHERE id=:id"),
                                      {"id": application}).scalar_one() is not None
            del source.folders[position]["合成简历.pdf"]
            assert catalog.synchronize()["deleted"] == 1
            assert connection.execute(text("SELECT deleted_at FROM application WHERE id=:id"),
                                      {"id": application}).scalar_one() is not None
            del source.folders[role_path]["开发岗位"]
            catalog.synchronize()
            assert connection.execute(text("SELECT hidden FROM job WHERE id=:id"), {"id": job}).scalar_one()
        transaction.rollback()


def test_ftp_negotiates_utf8_before_opening_the_catalog(monkeypatch):
    calls = []

    class Client:
        def __init__(self, encoding):
            calls.append(("encoding", encoding))

        def __enter__(self):
            return self

        def __exit__(self, *result):
            return None

        def connect(self, host, port, timeout):
            calls.append(("connect", port))

        def login(self, user, password):
            calls.append(("login", user))

        def sendcmd(self, command):
            calls.append(("option", command))
            return "200 UTF8 enabled"

        def cwd(self, path):
            calls.append(("cwd", path))

    monkeypatch.setattr(ftp, "FTP", Client)
    settings = Settings(resume_ftp_host="test.invalid", resume_ftp_user="fixture",
                        resume_ftp_password="fixture", resume_ftp_tls=False, resume_ftp_root="/岗位目录")
    with ftp.open_source(settings):
        assert calls[-2:] == [("option", "OPTS UTF8 ON"), ("cwd", "/岗位目录")]
