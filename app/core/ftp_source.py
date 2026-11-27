"""Windows FTP 目录及文件读取，兼容 MLSD 和 IIS 的 DOS 目录列表。"""
import codecs
import re
import ssl
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from ftplib import FTP, FTP_TLS, Error, error_perm
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4

from app.core.config import get_settings

ROOT_MARKER = ".resume-catalog-v1"
JOB_MARKER = ".resume-job-"
MAX_DEPTH = 20
MAX_FILE = 20 * 1024 * 1024
MIME = {
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
DOS_ENTRY = re.compile(r"^\d{2}-\d{2}-\d{2,4}\s+\d{1,2}:\d{2}(?:AM|PM)\s+(<DIR>|\d+)\s+(.+)$", re.I)
UNIX_ENTRY = re.compile(r"^([d-])[rwxstST-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$")
MODIFIED = re.compile(r"\d{14}(?:\.\d+)?")
DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


class CatalogError(Exception):
    """可安全返回给管理人员的目录错误。"""


@dataclass(frozen=True)
class Entry:
    name: str
    directory: bool
    size: int = 0
    modified: str = ""


def enabled(settings=None):
    return bool((settings or get_settings()).resume_ftp_host)


def valid_name(name):
    return bool(
        name and len(name.encode("utf-16-le")) <= 510 and name == name.strip()
        and not name.endswith(".") and name not in {".", ".."}
        and name.isprintable() and not any(char in name for char in '/\\<>:"|?*')
        and not DEVICE.match(name)
    )


def join(parent, name):
    return f"{parent}/{name}" if parent else name


def file_mime(name):
    return MIME.get(PurePosixPath(name).suffix.lower())


def check_root(entries):
    if not any(entry.name == ROOT_MARKER and not entry.directory for entry in entries):
        raise CatalogError("FTP 根目录标记缺失，请检查文件服务器配置")


class FtpSource:
    def __init__(self, client):
        self.client = client
        self.use_mlsd = True

    def list(self, path):
        location = path or "."
        if self.use_mlsd:
            try:
                entries = list(self.client.mlsd(location, facts=["type", "size", "modify"]))
            except error_perm as error:
                if str(error)[:3] not in {"500", "501", "502", "504"}:
                    raise
                self.use_mlsd = False
            else:
                result = []
                for name, facts in entries:
                    kind = facts.get("type", "").lower()
                    if kind in {"cdir", "pdir"} or "slink" in kind:
                        continue
                    if kind not in {"dir", "file"}:
                        raise CatalogError("FTP 返回了无法识别的目录项")
                    result.append(Entry(name, kind == "dir", int(facts.get("size", "0")), facts.get("modify", "")))
                return self._validated(result)
        result = []
        lines = []
        self.client.retrlines(f"LIST {location}", lines.append)
        for line in lines:
            if re.fullmatch(r"total\s+\d+", line):
                continue
            dos = DOS_ENTRY.fullmatch(line)
            unix = UNIX_ENTRY.fullmatch(line)
            if dos:
                size, name = dos.groups()
                directory = size.upper() == "<DIR>"
            elif unix:
                kind, size, name = unix.groups()
                directory = kind == "d"
            elif line.startswith("l"):
                continue
            else:
                raise CatalogError("FTP 返回了无法识别的目录列表")
            result.append(Entry(name, directory, 0 if directory else int(size)))
        return self._validated(result)

    @staticmethod
    def _validated(entries):
        names = set()
        result = []
        for entry in entries:
            if entry.name in {".", ".."}:
                continue
            key = entry.name.casefold()
            if not valid_name(entry.name) or key in names:
                raise CatalogError("FTP 目录含不兼容或重复的名称")
            names.add(key)
            result.append(entry)
        return result

    def fingerprint(self, path, entry):
        modified = entry.modified
        if not MODIFIED.fullmatch(modified):
            modified = self.modified(path)
        return entry.size, modified

    def modified(self, path):
        response = self.client.sendcmd(f"MDTM {path}")
        modified = response[4:]
        if not response.startswith("213 ") or not MODIFIED.fullmatch(modified):
            raise CatalogError("FTP 未返回有效的文件修改时间")
        return modified

    def read(self, path, size, modified):
        data = bytearray()

        def append(block):
            if len(data) + len(block) > size:
                raise CatalogError("FTP 文件在同步期间发生变化，请稍后重试")
            data.extend(block)

        self.client.retrbinary(f"RETR {path}", append)
        if len(data) != size or self.modified(path) != modified:
            raise CatalogError("FTP 文件在同步期间发生变化，请稍后重试")
        return bytes(data)

    def mkdir(self, path):
        self.client.mkd(path)

    def marker(self, path):
        self.client.storbinary(f"STOR {path}", BytesIO())

    def upload(self, folder, name, data, *, existing=None, listed=False):
        path = join(folder, name)
        if not listed:
            existing = next((entry for entry in self.list(folder) if entry.name.casefold() == name.casefold()), None)
        if existing is not None:
            if existing.directory or existing.size != len(data):
                raise CatalogError("FTP 已有同名文件，请重新选择文件")
            size, modified = self.fingerprint(path, existing)
            if self.read(path, size, modified) != data:
                raise CatalogError("FTP 已有同名文件，请重新选择文件")
            return
        temporary = join(folder, f".upload-{uuid4().hex}.tmp")
        try:
            self.client.storbinary(f"STOR {temporary}", BytesIO(data))
            self.client.rename(temporary, path)
        except Exception:
            with suppress(Error, OSError, EOFError):
                self.client.delete(temporary)
            raise


@contextmanager
def open_source(settings=None):
    settings = settings or get_settings()
    if not enabled(settings) or not settings.resume_ftp_user or not settings.resume_ftp_password:
        raise CatalogError("FTP 服务尚未配置")
    if any(char in settings.resume_ftp_root for char in "\r\n"):
        raise CatalogError("FTP 根目录配置无效")
    client = (
        FTP_TLS(context=ssl.create_default_context(cafile=settings.resume_ftp_ca_file or None),
                encoding=settings.resume_ftp_encoding)
        if settings.resume_ftp_tls else FTP(encoding=settings.resume_ftp_encoding)
    )
    with client:
        client.connect(settings.resume_ftp_host, settings.resume_ftp_port, timeout=15)
        client.login(settings.resume_ftp_user, settings.resume_ftp_password.get_secret_value())
        if settings.resume_ftp_tls:
            client.prot_p()
        if codecs.lookup(settings.resume_ftp_encoding).name == "utf-8":
            try:
                client.sendcmd("OPTS UTF8 ON")
            except error_perm as error:
                if str(error)[:3] not in {"500", "501", "502", "504"}:
                    raise
        client.cwd(settings.resume_ftp_root or "/")
        yield FtpSource(client)
