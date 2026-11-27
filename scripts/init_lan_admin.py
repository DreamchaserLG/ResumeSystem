"""初始化新建内网数据库的管理员口令，并停用仍使用默认口令的演示账号。"""
import getpass

from sqlalchemy import text

from app.core.db import owner_engine
from app.core.security import hash_password

DEFAULT_HASHES = {
    "admin@company.internal": "a58e2cf44d94d802b38d4324244008eb2c576de5bad9ff0abed1ce41cff7a1dc",
    "hr.tech@company.internal": "cbaf558d7dfdcb992a8c35b992273d94457c4ff99535694e0ea7754c2752ab37",
    "hr.market@company.internal": "b0bc000640f55044ea1b5d5da26a9484c27a5297e2e5c8e36900d0d5a576d2f3",
}
ADMIN_EMAIL = "admin@company.internal"


def initialize(password):
    if len(password) < 16 or password.isspace() or hash_password(password) == DEFAULT_HASHES[ADMIN_EMAIL]:
        raise SystemExit("请设置至少 16 个字符的新密码")
    with owner_engine.begin() as connection:
        changed = connection.execute(text("""
            UPDATE app_user SET password_hash=:new,perm_version=perm_version+1
            WHERE email=:email AND password_hash=:old
            RETURNING id
        """), {"email": ADMIN_EMAIL, "old": DEFAULT_HASHES[ADMIN_EMAIL], "new": hash_password(password)}).scalar_one_or_none()
        if changed is None:
            raise SystemExit("管理员口令已初始化或演示账号不存在")
        for email in DEFAULT_HASHES.keys() - {ADMIN_EMAIL}:
            connection.execute(text("""
                UPDATE app_user SET status='disabled',perm_version=perm_version+1
                WHERE email=:email AND password_hash=:old AND status='active'
            """), {"email": email, "old": DEFAULT_HASHES[email]})
    print("内网管理员账号已初始化")


if __name__ == "__main__":
    initialize(getpass.getpass("新的管理员密码："))
