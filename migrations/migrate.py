"""SQL 迁移运行器：按文件名顺序执行 migrations/sql/*.sql，已应用的不重复执行。

用法：python -m migrations.migrate
"""
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "sql"


def get_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://resume:resume@localhost:5434/resume"
    )


def ensure_registry(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def render(sql: str) -> str:
    """迁移占位符替换。生产部署通过环境变量注入，默认与 compose 保持一致。"""
    return sql.replace("${RS_APP_PASSWORD}", os.environ.get("RS_APP_PASSWORD", "rs_app123"))


def main() -> int:
    files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("no migration files found")
        return 0
    with psycopg.connect(get_dsn()) as cn:
        with cn.cursor() as cur:
            ensure_registry(cur)
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {r[0] for r in cur.fetchall()}
            pending = [f for f in files if f not in applied]
            if not pending:
                print(f"migrations up to date ({len(applied)} applied)")
                return 0
            for fname in pending:
                sql = render((MIGRATIONS_DIR / fname).read_text(encoding="utf-8"))
                print(f"applying {fname} ...")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (fname,)
                )
                cn.commit()
                print(f"  ok: {fname}")
        print("migration complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
