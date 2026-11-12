"""迁移幂等性：重复执行 migrate 应为 no-op。"""
import psycopg

from migrations.migrate import main


def test_migrations_idempotent(dsn, capsys):
    assert main() == 0  # 第一次（或已完成）执行
    assert main() == 0  # 重复执行应 no-op
    out = capsys.readouterr().out
    assert "up to date" in out or "migration complete" in out


def test_all_tables_have_rls(dsn):
    """业务表全部启用 RLS（01 §4 不变量：新表必附策略，CI 校验）。"""
    with psycopg.connect(dsn) as cn:
        cur = cn.cursor()
        cur.execute(
            """
            SELECT t.tablename
            FROM pg_tables t
            WHERE t.schemaname = 'public'
              AND t.tablename NOT IN ('schema_migrations')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_class c
                  WHERE c.relname = t.tablename AND c.relrowsecurity
              )
            """
        )
        missing = [r[0] for r in cur.fetchall()]
        assert missing == [], f"tables without RLS: {missing}"
