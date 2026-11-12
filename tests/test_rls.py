"""RLS 行为验证（02 §2.2：部门 HR 按授权部门过滤、无上下文全拒、事务级上下文提交后清除）。

数据场景：部门 1 与部门 2 各一条申请（独立候选人/岗位/版本）。
"""
import psycopg

from app.core.db import engine, rls_session
from sqlalchemy import text


def _setup_data(dsn):
    with psycopg.connect(dsn) as cn:
        cur = cn.cursor()
        # 幂等：先清理上一轮测试数据（按 FK 逆序、固定标记）
        cur.execute(
            """
            DELETE FROM application WHERE handling_department_id IN (
                SELECT id FROM department WHERE name IN ('测试部门一','测试部门二'));
            DELETE FROM resume_record WHERE version_id IN (
                SELECT v.id FROM resume_version v
                JOIN resume_document d ON d.id = v.document_id
                WHERE d.object_key IN ('k1','k2'));
            DELETE FROM resume_version WHERE document_id IN (
                SELECT id FROM resume_document WHERE object_key IN ('k1','k2'));
            DELETE FROM resume_document WHERE object_key IN ('k1','k2');
            DELETE FROM candidate c WHERE NOT EXISTS (
                SELECT 1 FROM application a WHERE a.candidate_id = c.id);
            DELETE FROM job WHERE title IN ('测试岗位一','测试岗位二');
            DELETE FROM department_admin WHERE user_id IN (
                SELECT id FROM app_user WHERE email = 'rls.test@company.internal');
            DELETE FROM app_user WHERE email = 'rls.test@company.internal';
            DELETE FROM department WHERE name IN ('测试部门一','测试部门二');
            """
        )
        cur.execute(
            """
            DO $$
            DECLARE d1 bigint; d2 bigint; c1 bigint; c2 bigint;
                    doc1 bigint; doc2 bigint; v1 bigint; v2 bigint; j1 bigint; j2 bigint;
            BEGIN
                INSERT INTO department (name) VALUES ('测试部门一') RETURNING id INTO d1;
                INSERT INTO department (name) VALUES ('测试部门二') RETURNING id INTO d2;
                INSERT INTO app_user (display_name, email, password_hash)
                    VALUES ('RLS测试HR', 'rls.test@company.internal', 'x') RETURNING id INTO j1;
                INSERT INTO department_admin (department_id, user_id) VALUES (d1, j1);
                INSERT INTO job (department_id, title) VALUES (d1, '测试岗位一') RETURNING id INTO j2;
                INSERT INTO job (department_id, title) VALUES (d2, '测试岗位二');
                INSERT INTO candidate DEFAULT VALUES RETURNING id INTO c1;
                INSERT INTO candidate DEFAULT VALUES RETURNING id INTO c2;
                INSERT INTO resume_document (candidate_id, object_key, mime_type, sha256)
                    VALUES (c1, 'k1', 'application/pdf', 'a1') RETURNING id INTO doc1;
                INSERT INTO resume_document (candidate_id, object_key, mime_type, sha256)
                    VALUES (c2, 'k2', 'application/pdf', 'a2') RETURNING id INTO doc2;
                INSERT INTO resume_version (document_id, version_no, parser_version)
                    VALUES (doc1, 1, 'v1') RETURNING id INTO v1;
                INSERT INTO resume_version (document_id, version_no, parser_version)
                    VALUES (doc2, 1, 'v1') RETURNING id INTO v2;
                UPDATE resume_version SET status='published', published_at=now() WHERE id IN (v1, v2);
                INSERT INTO application (job_id, candidate_id, submitted_department_id,
                    handling_department_id, submitted_version_id, active_version_id, status)
                    VALUES (j2, c1, d1, d1, v1, v1, 'active');
                INSERT INTO application (job_id, candidate_id, submitted_department_id,
                    handling_department_id, submitted_version_id, active_version_id, status)
                    VALUES ((SELECT id FROM job WHERE title='测试岗位二'), c2, d2, d2, v2, v2, 'active');
            END $$;
            """
        )
        cn.commit()


def test_rls_department_isolation(dsn):
    _setup_data(dsn)

    # 经 rs_app 角色 + 部门上下文查询（模拟应用路径）
    with rls_session(departments=[_dept_id("测试部门一")], user_id=999) as s:
        rows = s.execute(
            text("SELECT handling_department_id FROM application ORDER BY id")
        ).fetchall()
        assert len(rows) == 1  # 只见部门一

    # 无上下文 → 0 行（default deny）
    with rls_session() as s:
        n = s.execute(text("SELECT count(*) FROM application")).scalar_one()
        assert n == 0

    # 事务级上下文不跨请求（新会话默认无上下文）
    with rls_session(departments=[_dept_id("测试部门一")]) as s:
        pass
    from app.core.db import SessionLocal

    s2 = SessionLocal()
    try:
        n = s2.execute(text("SELECT count(*) FROM application")).scalar_one()
        assert n == 0, "RLS context leaked beyond transaction"
    finally:
        s2.close()


def test_rls_candidate_chain(dsn):
    """candidate 经 application 链继承部门可见性。"""
    dept1 = _dept_id("测试部门一")
    with rls_session(departments=[dept1]) as s:
        names = s.execute(
            text(
                "SELECT d.name FROM department d"
            )
        ).fetchall()  # 主数据可读
        assert len(names) >= 4
        # candidate 只见部门一挂的那位
        n = s.execute(text("SELECT count(*) FROM candidate")).scalar_one()
        assert n == 1


def _dept_id(name: str) -> int:
    with engine.connect() as cn:
        return cn.execute(
            text("SELECT id FROM department WHERE name = :n"), {"n": name}
        ).scalar_one()


def test_rls_version_does_not_follow_shared_candidate(dsn):
    from app.core.db import owner_engine

    _setup_data(dsn)
    dept1 = _dept_id("测试部门一")
    with owner_engine.connect() as cn:
        tx = cn.begin()
        versions = dict(cn.execute(text("""
            SELECT d.object_key,v.id FROM resume_version v
            JOIN resume_document d ON d.id=v.document_id WHERE d.object_key IN ('k1','k2')
        """)).all())
        cn.execute(text("""
            UPDATE resume_document SET candidate_id=(SELECT candidate_id FROM resume_document WHERE object_key='k1')
            WHERE object_key='k2'
        """))
        cn.execute(text("SET LOCAL ROLE rs_app"))
        cn.execute(text("SELECT set_config('app.departments',:dept,true)"), {"dept": str(dept1)})
        ids = cn.execute(text("SELECT id FROM resume_version WHERE id IN (:first,:second)"),
                         {"first": versions["k1"], "second": versions["k2"]}).scalars().all()
        assert ids == [versions["k1"]]
        tx.rollback()
