"""Build the isolated public showcase database from the checked-in folder plan.

Commands:
  python scripts/showcase_dataset.py audit
  python scripts/showcase_dataset.py prepare
  python scripts/showcase_dataset.py import
  python scripts/showcase_dataset.py verify
  python scripts/showcase_dataset.py dump

The script refuses to write to the normal resume database. All imports are idempotent
by a UUID derived from the folder and filename. "通用" is deliberately not imported.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLAN_PATH = ROOT / "frontend" / "src" / "data" / "showcasePlan.json"
SOURCE_ROOT = ROOT / "resume"
STORAGE_ROOT = ROOT / "showcase" / "storage"
DATABASE_NAME = "resume_showcase"
MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def plan_and_files():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    entries = []
    seen = set()
    for job in plan["jobs"]:
        for source in job["sources"]:
            folder = source["folder"]
            if folder in seen:
                raise SystemExit(f"来源文件夹重复分配：{folder}")
            seen.add(folder)
            path = SOURCE_ROOT / folder
            files = sorted(file for file in path.iterdir() if file.is_file())
            if len(files) != source["count"]:
                raise SystemExit(f"{folder}：表格 {source['count']} 份，实际 {len(files)} 份")
            if any(file.suffix.lower() not in MIME or file.stat().st_size > 20 * 1024 * 1024 for file in files):
                raise SystemExit(f"{folder} 包含不支持的格式或超过 20 MB 的文件")
            entries.extend((job, file) for file in files)
    actual = {item.name for item in SOURCE_ROOT.iterdir() if item.is_dir()}
    extras = actual - seen - set(plan["excludedFolders"])
    if extras:
        raise SystemExit("存在未映射的文件夹：" + "、".join(sorted(extras)))
    print(f"plan: {len({job['department'] for job in plan['jobs']})} departments, "
          f"{len(plan['jobs'])} jobs, {len(entries)} mapped resumes; "
          f"excluded: {', '.join(plan['excludedFolders'])}", flush=True)
    return plan, entries


def configure():
    from sqlalchemy.engine import make_url
    from app.core.config import get_settings

    settings = get_settings()
    owner = make_url(settings.database_url).set(database=DATABASE_NAME)
    app = make_url(settings.app_database_url).set(database=DATABASE_NAME)
    os.environ["DATABASE_URL"] = owner.render_as_string(hide_password=False)
    os.environ["APP_DATABASE_URL"] = app.render_as_string(hide_password=False)
    os.environ["RS_APP_PASSWORD"] = app.password or "rs_app123"
    os.environ["STORAGE_ROOT"] = str(STORAGE_ROOT)
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6380/5"
    os.environ["CELERY_BROKER_URL"] = "redis://127.0.0.1:6380/6"
    os.environ["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6380/7"
    get_settings.cache_clear()
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return owner


def create_database(owner):
    import psycopg
    from psycopg import sql

    admin_dsn = owner.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname=%s", (DATABASE_NAME,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'").format(sql.Identifier(DATABASE_NAME)))
            print(f"created isolated database {DATABASE_NAME}", flush=True)
        else:
            encoding = exists[0].decode() if isinstance(exists[0],bytes) else exists[0]
            if encoding != "UTF8":
                raise SystemExit(f"{DATABASE_NAME} 编码为 {exists[0]}，需要空的 UTF8 数据库。")
            print(f"using existing isolated database {DATABASE_NAME}", flush=True)


def prepare(plan, owner):
    from sqlalchemy import create_engine, text

    create_database(owner)
    subprocess.run([sys.executable, "-m", "migrations.migrate"], cwd=ROOT, env=os.environ.copy(), check=True)
    engine = create_engine(owner.set(drivername="postgresql+psycopg"))
    seed_departments = {
        "信息技术部": 1,
        "人力资源部": 2,
        "百货事业部": 3,
        "财务部 / 资金投资部": 4,
    }
    seed_jobs = {
        ("信息技术部", "软件开发工程师"): 1,
        ("信息技术部", "系统运维与网络工程师"): 2,
        ("百货事业部", "招商与品类管理专员"): 3,
    }
    with engine.begin() as conn:
        company_count = conn.execute(text("SELECT count(*) FROM recruiting_company")).scalar_one()
        department_count = conn.execute(text("SELECT count(*) FROM department")).scalar_one()
        job_count = conn.execute(text("SELECT count(*) FROM job")).scalar_one()
        if company_count != 1 or department_count not in (4, 11) or job_count not in (3, len(plan["jobs"])):
            raise SystemExit("showcase 数据库已有非预期业务数据，停止改写。")
        conn.execute(text("UPDATE recruiting_company SET name=:name WHERE id=1"), {"name":plan["company"]})
        departments = list(dict.fromkeys(job["department"] for job in plan["jobs"]))
        for name in departments:
            fixed_id = seed_departments.get(name)
            if fixed_id:
                conn.execute(text("""
                    UPDATE department SET name=:name,company_id=1,status='active' WHERE id=:id
                """), {"name":name,"id":fixed_id})
            else:
                conn.execute(text("""
                    INSERT INTO department(name,company_id)
                    SELECT :name,1 WHERE NOT EXISTS(SELECT 1 FROM department WHERE name=:name)
                """), {"name":name})
        ids = {row.name:row.id for row in conn.execute(text("SELECT id,name FROM department"))}
        for job in plan["jobs"]:
            params = {"department":ids[job["department"]],"title":job["title"],"jd":job["jd"]}
            fixed_id = seed_jobs.get((job["department"],job["title"]))
            if fixed_id:
                conn.execute(text("""
                    UPDATE job SET department_id=:department,title=:title,jd=:jd,requirements=:jd,
                        status='open',planned_headcount=NULL,recruitment_type='social',updated_at=now()
                    WHERE id=:id
                """), {**params,"id":fixed_id})
            else:
                conn.execute(text("""
                    INSERT INTO job(department_id,title,jd,requirements,status,recruitment_type)
                    SELECT :department,:title,:jd,:jd,'open','social'
                    WHERE NOT EXISTS(SELECT 1 FROM job WHERE department_id=:department AND title=:title)
                """), params)
        conn.execute(text("UPDATE app_user SET display_name='信息技术部 HR' WHERE id=2"))
        conn.execute(text("UPDATE app_user SET display_name='百货事业部 HR' WHERE id=3"))
        final_jobs = conn.execute(text("SELECT count(*) FROM job")).scalar_one()
        if final_jobs != len(plan["jobs"]):
            raise SystemExit(f"岗位数量应为 {len(plan['jobs'])}，实际 {final_jobs}")
    engine.dispose()
    print(f"prepared {len(departments)} departments and {final_jobs} jobs", flush=True)


def import_resumes(plan, entries):
    # Import the real files through the same API path as the UI. Parsing is run
    # synchronously here so that a completed run means the database is ready to dump.
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.api import uploads
    from app.core.db import owner_engine
    from app.main import app
    from worker.tasks.parse import process_resume

    logging.getLogger("app").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("worker.parse").setLevel(logging.CRITICAL)
    uploads.schedule_parse = lambda version: process_resume.run(version)
    with owner_engine.connect() as conn:
        jobs = {(row.department,row.title):row.id for row in conn.execute(text("""
            SELECT j.id,d.name AS department,j.title FROM job j JOIN department d ON d.id=j.department_id
        """))}
    errors = []
    with TestClient(app) as client:
        credentials = {
            "email":"admin@company.internal",
            "password":os.environ.get("SHOWCASE_ADMIN_PASSWORD","admin123"),
        }
        login = client.post("/api/v1/auth/login", json=credentials)
        if login.status_code == 409:
            challenge = login.json().get("detail", {})
            if challenge.get("code") == "login_conflict":
                login = client.post("/api/v1/auth/login", json={**credentials,"replace_login":challenge["replace_login"]})
        login.raise_for_status()
        headers = {"X-Session-Token":login.json()["token"]}
        for index,(job,file) in enumerate(entries,1):
            job_id = jobs[(job["department"],job["title"])]
            key = uuid5(NAMESPACE_URL, "resume-showcase-v1/" + file.relative_to(SOURCE_ROOT).as_posix())
            try:
                with file.open("rb") as source:
                    response = client.post(
                        f"/api/v1/applications?job_id={job_id}&import_request={key}",
                        headers=headers,
                        files={"file":(file.name,source,MIME[file.suffix.lower()])},
                        timeout=180,
                    )
                response.raise_for_status()
                result = response.json()
                if result.get("status") != "published":
                    process_resume.run(result["version_id"])
            except Exception as exc:  # continue collecting exact files for retry
                errors.append({"file":file.relative_to(SOURCE_ROOT).as_posix(),"error":str(exc)[:240]})
            if index % 10 == 0 or index == len(entries):
                print(f"imported {index}/{len(entries)}, request errors {len(errors)}", flush=True)
        client.post("/api/v1/auth/logout", headers=headers)
    if errors:
        error_file = ROOT / ".runtime" / "showcase-import-errors.json"
        error_file.write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
        raise SystemExit(f"{len(errors)} import requests failed; see {error_file}")
    print("all import requests completed", flush=True)


def verify(plan, owner):
    from sqlalchemy import create_engine, text

    engine = create_engine(owner.set(drivername="postgresql+psycopg"))
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT d.name AS department,j.title,count(a.id) AS resumes,
                   count(a.id) FILTER(WHERE v.status='published') AS published,
                   count(a.id) FILTER(WHERE v.status='failed') AS failed
            FROM job j JOIN department d ON d.id=j.department_id
            LEFT JOIN application a ON a.job_id=j.id AND a.deleted_at IS NULL
            LEFT JOIN resume_version v ON v.id=a.submitted_version_id
            GROUP BY d.name,j.title
        """)).mappings().all()
        by_key = {(row["department"],row["title"]):row for row in rows}
        mismatches = []
        for job in plan["jobs"]:
            expected = sum(source["count"] for source in job["sources"])
            row = by_key.get((job["department"],job["title"]))
            count = row["resumes"] if row else 0
            print(f"{job['department']} / {job['title']}: {count}/{expected}, "
                  f"published {row['published'] if row else 0}, failed {row['failed'] if row else 0}")
            if count != expected or (row and row["failed"]):
                mismatches.append(job["title"])
        total = conn.execute(text("SELECT count(*) FROM application WHERE deleted_at IS NULL")).scalar_one()
        print(f"total imported: {total}; mismatched jobs: {len(mismatches)}", flush=True)
        if total != 403 or mismatches:
            raise SystemExit("showcase import does not match the source plan")
    engine.dispose()


def dump(owner) -> Path:
    """Write a restorable SQL snapshot (schema created by migrations, data by COPY)."""
    import gzip

    import psycopg
    from sqlalchemy.engine import make_url

    dsn = owner.set(database=DATABASE_NAME).render_as_string(hide_password=False)
    target = ROOT / "db" / "showcase.sql.gz"
    target.parent.mkdir(exist_ok=True)
    with psycopg.connect(dsn) as conn, gzip.open(target, "wt", encoding="utf-8", newline="\n") as out:
        tables = [row[0] for row in conn.execute("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relispartition
              AND c.relname NOT LIKE 'pg_%' AND c.relname<>'schema_migrations' ORDER BY c.relname
        """)]
        columns = {}
        for table in tables:
            columns[table] = [row[0] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,))]
        out.write("-- ResumeSystem showcase snapshot. Restore with: psql -f showcase.sql.gz -v ON_ERROR_STOP=1\n")
        out.write("-- Target database must already be migrated (python -m migrations.migrate).\n")
        out.write("BEGIN;\nSET session_replication_role = replica;\n")
        out.write("TRUNCATE " + ",".join(f'public."{table}"' for table in tables) + " CASCADE;\n")
        total = 0
        for table in tables:
            cols = ",".join(f'"{column}"' for column in columns[table])
            with conn.cursor() as cur, cur.copy(f'COPY public."{table}" ({cols}) TO STDOUT (FORMAT csv)') as copy:
                out.write(f'COPY public."{table}" ({cols}) FROM stdin (FORMAT csv);\n')
                for chunk in copy:
                    out.write(bytes(chunk).decode("utf-8"))
            out.write("\\.\n")
            total += conn.execute(f'SELECT count(*) FROM public."{table}"').fetchone()[0]
        for table in tables:
            for column in columns[table]:
                seq = conn.execute(
                    "SELECT pg_get_serial_sequence(%s,%s)", (f'public."{table}"', column)).fetchone()[0]
                if not seq:
                    continue
                out.write(f"SELECT setval('{seq}', COALESCE((SELECT max(\"{column}\") FROM public.\"{table}\"),1)"
                          f", (SELECT max(\"{column}\") FROM public.\"{table}\") IS NOT NULL);\n")
        out.write("SET session_replication_role = origin;\nCOMMIT;\n")
        print(f"dumped {len(tables)} tables / {total} rows to {target.relative_to(ROOT)} "
              f"({target.stat().st_size/1048576:.1f} MB gzip)", flush=True)
    return target


def load_snapshot(conn, path: Path) -> None:
    """Execute the gzipped snapshot: plain statements plus COPY ... FROM stdin blocks."""
    import gzip

    payload = gzip.open(path, "rb").read()
    position = 0
    copies = 0
    while position < len(payload):
        newline = payload.find(b"\n", position)
        if newline < 0:
            break
        line = payload[position:newline]
        if not line.strip() or line.startswith(b"--"):
            position = newline + 1
            continue
        if line.startswith(b"COPY "):
            # 空表时结束标记紧跟表头换行，因此从表头换行处开始查找
            end = payload.find(b"\n\\.\n", newline)
            if end < 0:
                raise SystemExit("快照的 COPY 数据段缺少结束标记，文件可能损坏")
            if not line.startswith(b'COPY public."schema_migrations" '):
                with conn.cursor() as cursor, cursor.copy(line.decode("utf-8")) as copy:
                    copy.write(payload[newline + 1:end + 1] if end > newline else b"")
                copies += 1
            position = end + 4
            continue
        statement = bytearray(line)
        position = newline + 1
        while not statement.rstrip().endswith(b";"):
            following = payload.find(b"\n", position)
            if following < 0:
                break
            statement += b"\n" + payload[position:following]
            position = following + 1
        sql = statement.decode("utf-8")
        if sql.startswith("TRUNCATE "):
            sql = sql.replace(',public."schema_migrations"', '')
        conn.execute(sql)
    print(f"loaded {copies} COPY blocks from {path.name}", flush=True)


def restore(owner, database: str) -> None:
    """Rebuild a database from db/showcase.sql.gz: create → migrate → load."""
    import psycopg

    source = ROOT / "db" / "showcase.sql.gz"
    if not source.exists():
        raise SystemExit(f"缺少快照文件 {source.relative_to(ROOT)}，请先运行 dump")
    admin = owner.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            conn.execute(f'CREATE DATABASE "{database}" WITH TEMPLATE template0 ENCODING \'UTF8\''
                         " LC_COLLATE 'C' LC_CTYPE 'C'")
            print(f"created database {database}", flush=True)
    env = os.environ.copy()
    env["DATABASE_URL"] = owner.set(database=database).render_as_string(hide_password=False)
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    app_url = make_url(get_settings().app_database_url).set(database=database)
    env["APP_DATABASE_URL"] = app_url.render_as_string(hide_password=False)
    env["RS_APP_PASSWORD"] = app_url.password or env.get("RS_APP_PASSWORD", "rs_app123")
    subprocess.run([sys.executable, "-m", "migrations.migrate"], cwd=ROOT, env=env, check=True)
    with psycopg.connect(owner.set(database=database).render_as_string(hide_password=False)) as conn:
        with conn.transaction():
            load_snapshot(conn, source)
    with psycopg.connect(owner.set(database=database).render_as_string(hide_password=False)) as conn:
        applications = conn.execute("SELECT count(*) FROM application WHERE deleted_at IS NULL").fetchone()[0]
        published = conn.execute("SELECT count(*) FROM resume_version WHERE status='published'").fetchone()[0]
        jobs = conn.execute("SELECT count(*) FROM job").fetchone()[0]
        departments = conn.execute("SELECT count(*) FROM department").fetchone()[0]
        print(f"restored {database}: {departments} departments, {jobs} jobs, "
              f"{applications} applications, {published} published", flush=True)
        if (departments, jobs, applications, published) != (11, 24, 403, 403):
            raise SystemExit("恢复结果与展示数据集不一致")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("audit","prepare","import","verify","dump","restore"))
    parser.add_argument("--limit",type=int,default=None,help="import only the first N files for a smoke test")
    parser.add_argument("--database",default=None,help="restore 的目标库名，默认取 DATABASE_URL 指向的库")
    args = parser.parse_args()
    if args.command == "restore":
        from sqlalchemy.engine import make_url

        from app.core.config import get_settings

        target = make_url(get_settings().database_url)
        restore(target, args.database or target.database)
        return
    plan, entries = plan_and_files()
    if args.command == "audit":
        return
    owner = configure()
    if args.command == "prepare":
        prepare(plan,owner)
    elif args.command == "import":
        import_resumes(plan,entries[:args.limit] if args.limit else entries)
    elif args.command == "verify":
        verify(plan,owner)
    elif args.command == "dump":
        dump(owner)




if __name__ == "__main__":
    main()
