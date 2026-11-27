"""把本机导出的岗位清单（含 JD）同步到部署库，并使 FTP 目录挂回对应岗位。

用法（在 api 容器内执行）：
    python3 scripts/sync_local_jobs.py deploy/seed/jobs-20261127.json [--dry-run]

行为：
1. 以 department.name 匹配部署库部门；缺失的部门直接报错，不做静默创建。
2. 按 title 匹配已有岗位：命中则更新全部 JD 字段与状态；未命中则新建。
3. 删除部署库中多出且没有任何简历关联的岗位（本机不存在的样例数据），
   有简历关联的多余岗位保留并打印告警，避免误伤业务数据。
4. 把 ftp_catalog_folder 中同名目录的 job_id 指向新岗位（含全角/半角符号归一化）。
5. 全部写入在一个事务里完成，任何异常整体回滚。
"""
import json
import re
import sys

from sqlalchemy import text

from app.core.db import owner_engine

SLASH = str.maketrans({"/": "／", "｜": "|"})


def normalize(title):
    """岗位名归一化：全角斜杠与半角斜杠视为同一岗位。"""
    return re.sub(r"\s+", "", (title or "")).replace("/", "／").casefold()


def main(path, dry_run=False):
    jobs = json.load(open(path, encoding="utf-8"))
    with owner_engine.begin() as cn:
        departments = {row.name: row.id for row in cn.execute(text("SELECT id,name FROM department"))}
        missing = sorted({job["department"] for job in jobs} - set(departments))
        if missing:
            raise SystemExit("部署库缺少部门：" + "、".join(missing))

        existing = cn.execute(text("SELECT id,title FROM job")).all()
        by_title = {normalize(row.title): row.id for row in existing}

        created, updated = [], []
        for job in jobs:
            payload = {
                "title": job["title"],
                "department_id": departments[job["department"]],
                "jd": job.get("jd") or "",
                "requirements": job.get("requirements") or "",
                "required_skills": job.get("required_skills") or [],
                "experience_tags": job.get("experience_tags") or [],
                "recruitment_type": job.get("recruitment_type") or "social",
                "planned_headcount": job.get("planned_headcount"),
                "status": job.get("status") or "paused",
            }
            job_id = by_title.get(normalize(job["title"]))
            if job_id:
                sets = ",".join(f"{key}=:{key}" for key in payload if key != "title")
                cn.execute(text(f"UPDATE job SET {sets},updated_at=now() WHERE id=:id"),
                           {**payload, "id": job_id})
                updated.append((job_id, job["title"]))
            else:
                keys = list(payload)
                job_id = cn.execute(text(
                    f"INSERT INTO job({','.join(keys)}) "
                    f"VALUES({','.join(':' + key for key in keys)}) RETURNING id"
                ), payload).scalar_one()
                created.append((job_id, job["title"]))
                by_title[normalize(job["title"])] = job_id

            rounds = job.get("rounds") or []
            if rounds:
                cn.execute(text("DELETE FROM job_interview_round WHERE job_id=:id"), {"id": job_id})
                for position, name in enumerate(rounds, start=1):
                    cn.execute(text(
                        "INSERT INTO job_interview_round(job_id,name,position) VALUES(:id,:name,:position)"
                    ), {"id": job_id, "name": name, "position": position})

        keep = {job_id for job_id, _ in created} | {job_id for job_id, _ in updated}
        extras = cn.execute(text("""
            SELECT j.id,j.title,count(a.id) AS resumes
            FROM job j LEFT JOIN application a ON a.job_id=j.id AND a.deleted_at IS NULL
            WHERE j.id<>ALL(CAST(:keep AS bigint[]))
            GROUP BY j.id,j.title ORDER BY j.id
        """), {"keep": list(keep)}).all()
        removed, kept = [], []
        for row in extras:
            if row.resumes:
                kept.append((row.id, row.title, row.resumes))
                continue
            cn.execute(text("DELETE FROM job_interview_round WHERE job_id=:id"), {"id": row.id})
            cn.execute(text("DELETE FROM job WHERE id=:id"), {"id": row.id})
            removed.append((row.id, row.title))

        # 目录表约束：挂岗位的目录必须有 department_id 且路径含斜杠。
        job_department = {}
        for job in jobs:
            job_id = by_title.get(normalize(job["title"]))
            if job_id:
                job_department[job_id] = departments[job["department"]]

        folders = cn.execute(text(
            "UPDATE ftp_catalog_folder SET job_id=NULL WHERE job_id IS NOT NULL RETURNING path"
        )).rowcount
        linked, skipped = 0, []
        for row in cn.execute(text("SELECT path FROM ftp_catalog_folder WHERE job_id IS NULL")).all():
            title = row.path.split("/")[-1]
            job_id = by_title.get(normalize(title))
            if not job_id:
                continue
            if "/" not in row.path:
                skipped.append((row.path, "根级目录不能挂岗位"))
                continue
            owner = cn.execute(text(
                "SELECT coalesce(department_id,(SELECT department_id FROM job WHERE id=:job)) AS dep"
            ), {"job": job_id}).scalar_one()
            department = owner or job_department.get(job_id)
            if department is None:
                skipped.append((row.path, "岗位无部门"))
                continue
            cn.execute(text(
                "UPDATE ftp_catalog_folder SET job_id=:job,department_id=:dep WHERE path=:path"
            ), {"job": job_id, "dep": department, "path": row.path})
            linked += 1

        if dry_run:
            cn.rollback()
            print("[dry-run] 已回滚，未落库")

    print(f"新建 {len(created)} / 更新 {len(updated)} / 删除空岗位 {len(removed)} / 目录重挂 {linked}")
    for item in created:
        print("  +", item[1])
    for item in removed:
        print("  -", item[1])
    for job_id, title, resumes in kept:
        print(f"  ! 保留（有 {resumes} 份简历关联）：{title}")
    for path, reason in skipped:
        print(f"  ? 跳过目录 {path}：{reason}")
    if folders:
        print(f"  已解除 {folders} 个目录的原岗位绑定后重新匹配")


if __name__ == "__main__":
    args = [item for item in sys.argv[1:] if not item.startswith("--")]
    main(args[0], dry_run="--dry-run" in sys.argv[1:])
