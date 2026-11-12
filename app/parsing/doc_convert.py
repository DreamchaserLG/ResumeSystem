"""Convert an old Word document with a fresh LibreOffice profile and no IP sockets."""
import ctypes
import errno
import os
from pathlib import Path
import shutil
import sys


class Argument(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def deny_network():
    lib = ctypes.CDLL("libseccomp.so.2")
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                          ctypes.c_uint, ctypes.POINTER(Argument)]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)
    if not context:
        raise OSError("无法创建转换隔离环境")
    try:
        syscall = lib.seccomp_syscall_resolve_name(b"socket")
        for family in (2, 10):
            # AF_INET and AF_INET6. Unix sockets remain available to LibreOffice.
            if lib.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, syscall, 1,
                                          ctypes.byref(Argument(0, 4, family, 0))) != 0:
                raise OSError("无法配置转换隔离规则")
        if lib.seccomp_load(context) != 0:
            raise OSError("无法启用转换隔离")
    finally:
        lib.seccomp_release(context)


def main():
    path, directory = map(Path, sys.argv[1:3])
    extension = sys.argv[3] if len(sys.argv) > 3 else "docx"
    if extension not in {"docx", "pdf"}:
        raise ValueError("不支持的预览转换格式")
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise FileNotFoundError("旧版 DOC 转换需要 LibreOffice")
    profile = (directory / "profile").resolve().as_uri()
    deny_network()
    os.execv(executable, [executable, "-env:UserInstallation=" + profile, "--headless", "--safe-mode",
                          "--norestore", "--convert-to", extension, "--outdir", str(directory), str(path)])


if __name__ == "__main__":
    main()
