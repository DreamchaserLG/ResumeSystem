"""Import the selected roster and its verifiable job descriptions from local exports."""
import argparse
from datetime import date
import json
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.extraction import norm
from migrations.migrate import get_dsn

SOURCE_SYSTEM = "recruitment_export"
MISSING_JD = "（系统中该职位暂未填写 JD）"
DETAIL_HEADING = re.compile(r"^#{2,3}\s+\d+\.\s+(.+?)\s*$", re.M)
DUTIES_HEADING = re.compile(r"(?m)^\s*(?:岗位职责|职位描述|主要职责)\s*[:：]?\s*\n")
REQUIREMENTS_HEADING = re.compile(r"(?m)^\s*(?:任职要求|任职资格)\s*[:：]?\s*")
METADATA = re.compile(r"(职位编码|需求人数|工作地点|职位性质|工作经验|薪资)\s*[:：]\s*([^；;]+)")


def title_key(value):
    return norm(value).replace(" ", "")


def table_rows(content):
    columns = None
    for line in content.splitlines():
        if not line.startswith("|"):
            if columns:
                return
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if columns is None:
            if {"职位名称", "职位编码", "工作地点"} <= set(cells):
                columns = cells
            continue
        if all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        yield dict(zip(columns, cells, strict=True))


def parse_description(title, raw):
    raw = "\n".join(line.strip() for line in raw.splitlines()).strip()
    raw = re.sub(r"\s+立即沟通\s*$", "", raw)
    if not raw or raw == MISSING_JD:
        return "", ""
    requirements = REQUIREMENTS_HEADING.search(raw)
    if title == "多元化集团公司总部接待专员" and not requirements:
        requirements = re.search(r"(?m)^3、特别说明[:：]", raw)
    if not requirements:
        raise ValueError(f"无法核对 JD 的职责与要求分界：{title}")
    duties = raw[:requirements.start()].strip()
    heading = DUTIES_HEADING.search(duties + "\n")
    if heading:
        duties = duties[heading.end():].strip()
    requirements_text = raw[requirements.end():].strip()
    if not duties or not requirements_text or max(len(duties), len(requirements_text)) > 4000:
        raise ValueError(f"JD 段落缺失或超过字段长度：{title}")
    return duties, re.sub(r"\n{3,}", "\n\n", requirements_text)


def read_document(path):
    content = path.read_text(encoding="utf-8-sig")
    exported = date.fromisoformat(re.search(r"导出时间[：:]\s*(\d{4}-\d{2}-\d{2})", content)[1])
    blocks = DETAIL_HEADING.split(content)
    details = {}
    for heading, block in zip(blocks[1::2], blocks[2::2], strict=True):
        lines = re.split(r"(?m)^---\s*$", block, maxsplit=1)[0].strip().splitlines()
        if not lines or not lines[0].startswith("职位编码："):
            raise ValueError(f"缺少职位元数据：{heading}")
        key = title_key(heading)
        if key in details:
            raise ValueError(f"重复的 JD 标题：{heading}")
        details[key] = (dict(METADATA.findall(lines[0])), "\n".join(lines[1:]).strip())
    rows = {}
    for row in table_rows(content):
        title, code = row["职位名称"], row["职位编码"]
        if not re.fullmatch(r"MJ\d{6}", code) or code in rows:
            raise ValueError(f"职位编码缺失或重复：{title}")
        fields, raw = details[title_key(title)]
        if fields["职位编码"] not in (code, "None"):
            raise ValueError(f"一览表与正文职位编码不一致：{title}")
        location = row["工作地点"] if row["工作地点"] != "-" else None
        if location and fields.get("工作地点", location) != location:
            raise ValueError(f"一览表与正文工作地点不一致：{title}")
        headcount = fields.get("需求人数", row.get("需求人数"))
        jd_status = row.get("JD 状态")
        if jd_status and (raw == MISSING_JD) != (jd_status == "未填写"):
            raise ValueError(f"JD 状态与正文不一致：{title}")
        rows[code] = {"title": title, "category": row.get("类别"), "jd_status": jd_status, "code": code,
                      "detail_code": fields["职位编码"], "source": path.name, "exported_on": exported.isoformat(),
                      "location": location or fields.get("工作地点"), "employment_type": fields.get("职位性质"),
                      "headcount": int(headcount) if headcount and headcount != "-" else None,
                      "experience": fields.get("工作经验"), "salary": fields.get("薪资"), "raw_jd": raw}
    if set(details) != {title_key(row["title"]) for row in rows.values()}:
        raise ValueError(f"岗位一览与正文数量或名称不一致：{path.name}")
    return rows


def plan_entries(primary, supplement):
    primary_rows = read_document(primary)
    supplement_rows = read_document(supplement) if supplement else {}
    if not primary_rows:
        raise ValueError("附件中未检索到岗位")
    for code, row in primary_rows.items():
        title = row["title"]
        if row["category"] not in ("百货/商业", "总部"):
            raise ValueError(f"未配置部门映射：{row['category']}")
        other = supplement_rows.get(code)
        if other and title_key(other["title"]) != title_key(title):
            raise ValueError(f"补充清单里的同编码岗位名称不同：{code}")
        source = row
        if row["raw_jd"] == MISSING_JD and other and other["raw_jd"] not in ("", MISSING_JD):
            source = other
        jd, requirements = parse_description(title, source["raw_jd"])
        data = {key: row[key] if row[key] is not None else (other or {}).get(key)
                for key in ("location", "employment_type", "headcount")}
        if data["headcount"] is not None and not 1 <= data["headcount"] <= 5:
            raise ValueError(f"招聘人数超出 1–5：{title}")
        department = ("百货事业部" if row["category"] == "百货/商业"
                      else "法务部" if code == "MJ000038" else "新百总部")
        yield dict(data, title=title, department=department, code=code, category=row["category"],
                   jd=jd, requirements=requirements,
                   snapshot={"primary": row, "supplement": other,
                             "jd_source": source["source"] if jd else None})


def recruiter_for(entry, managers):
    department = entry["department"]
    available = managers[department]
    if len(available) == 1:
        return available[0]["id"]
    expected = "信息技术部 HR" if entry["code"] == "MJ000093" else "百货事业部 HR"
    people = [person["id"] for person in available if person["display_name"] == expected]
    if len(people) != 1:
        raise ValueError(f"请核对部门现有招聘负责人：{department}")
    return people[0]


def import_entries(connection, entries, *, commit):
    connection.execute("SELECT pg_advisory_xact_lock(721379,34)")
    departments = {row["name"]: row for row in connection.execute("SELECT id,name,status FROM department FOR UPDATE")}
    managers = {name: connection.execute("""
        SELECT u.id,u.display_name FROM department_admin a JOIN app_user u ON u.id=a.user_id
        WHERE a.department_id=%s AND u.status='active' ORDER BY u.id
    """, (departments[name]["id"],)).fetchall() for name in {entry["department"] for entry in entries}}
    existing = connection.execute("SELECT * FROM job ORDER BY id FOR UPDATE").fetchall()
    by_id = {job["id"]: job for job in existing}
    mappings = {row["source_job_id"]: row for row in connection.execute("""
        SELECT source_job_id,job_id,posting_snapshot FROM job_source_mapping WHERE source_system=%s
    """, (SOURCE_SYSTEM,))}
    application_counts = {row["job_id"]: row["total"] for row in connection.execute("""
        SELECT job_id,count(*) AS total FROM application WHERE deleted_at IS NULL AND job_id IS NOT NULL GROUP BY job_id
    """)}
    changed = False
    summary = []
    for entry in entries:
        title, code, name = (entry[key] for key in ("title", "code", "department"))
        department = departments[name]
        if not managers[name] or department["status"] != "active" and name != "百货事业部":
            raise ValueError(f"目标部门不可用：{name}")
        mapping = mappings.get(code)
        candidates = ([by_id[mapping["job_id"]]] if mapping else
                      [job for job in existing if title_key(job["title"]) == title_key(title)])
        if len(candidates) > 1:
            raise ValueError(f"同名岗位存在多条记录，请核对：{title}")
        job = next(iter(candidates), None)
        if job and title_key(job["title"]) != title_key(title):
            raise ValueError(f"来源编码已绑定其他岗位：{code}")
        allowed_departments = {department["id"]}
        if name == "百货事业部":
            allowed_departments.add(departments["新百总部"]["id"])
        if job and job["department_id"] not in allowed_departments:
            raise ValueError(f"库内同名岗位归属其他部门：{title}")

        if job and mapping and mapping["posting_snapshot"] == entry["snapshot"]:
            summary.append({"code": code, "title": title, "id": job["id"], "operation": "unchanged"})
            continue
        params = dict(entry, id=job["id"] if job else None, department_id=department["id"],
                      recruiter=job["recruiter_user_id"] if job and job["recruiter_user_id"] else recruiter_for(entry, managers))
        summary.append({"code": code, "title": title, "department": name, "id": params["id"],
                        "operation": "update" if job else "insert", "department_change": bool(job and job["department_id"] != department["id"]),
                        "linked_applications": application_counts.get(params["id"], 0),
                        "jd_source": entry["snapshot"]["jd_source"], "jd_chars": len(entry["jd"]),
                        "requirements_chars": len(entry["requirements"]), "headcount": entry["headcount"],
                        "work_location": entry["location"]})
        if not commit:
            continue
        if department["status"] != "active":
            connection.execute("UPDATE department SET status='active' WHERE id=%s AND status='disabled'", (department["id"],))
        if job:
            job_id = job["id"]
            connection.execute("""
                UPDATE job SET department_id=%(department_id)s, source_category=%(category)s,
                    jd=coalesce(nullif(%(jd)s,''),jd), requirements=coalesce(nullif(%(requirements)s,''),requirements),
                    planned_headcount=coalesce(%(headcount)s,planned_headcount), work_location=coalesce(%(location)s,work_location),
                    employment_type=coalesce(%(employment_type)s,employment_type), recruiter_user_id=%(recruiter)s,
                    updated_at=now() WHERE id=%(id)s
            """, params)
        else:
            job_id = connection.execute("""
                INSERT INTO job(department_id,title,jd,requirements,source_category,work_location,employment_type,
                                planned_headcount,recruiter_user_id,recruitment_type,status)
                VALUES (%(department_id)s,%(title)s,nullif(%(jd)s,''),%(requirements)s,%(category)s,%(location)s,
                        %(employment_type)s,%(headcount)s,%(recruiter)s,'social','open') RETURNING id
            """, params).fetchone()["id"]
            rounds = ("一面", "终面") if code in ("MJ000085", "MJ000044", "MJ000093") else ("一面", "二面", "终面")
            with connection.cursor() as cur:
                cur.executemany("INSERT INTO job_interview_round(job_id,name,position) VALUES (%s,%s,%s)",
                                [(job_id, round_name, position) for position, round_name in enumerate(rounds, 1)])
        connection.execute("""
            INSERT INTO job_source_mapping(source_system,source_job_id,job_id,posting_snapshot) VALUES (%s,%s,%s,%s)
            ON CONFLICT(source_system,source_job_id) DO UPDATE SET posting_snapshot=EXCLUDED.posting_snapshot
        """, (SOURCE_SYSTEM, code, job_id, Jsonb(entry["snapshot"])))
        connection.execute("""
            INSERT INTO audit_log(actor_id,action,target_type,target_id,detail)
            VALUES (NULL,'job_directory_import','job',%s,%s)
        """, (str(job_id), Jsonb({"source_code": code, "before": {
            key: job[key] for key in ("department_id", "jd", "requirements", "planned_headcount",
                                      "recruiter_user_id", "source_category", "work_location", "employment_type")
        } if job else None})))
        summary[-1]["id"] = job_id
        changed = True
    if changed:
        connection.execute("UPDATE search_state SET search_revision=search_revision+1 WHERE singleton")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary", type=Path)
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    entries = list(plan_entries(args.primary, args.supplement))
    with psycopg.connect(get_dsn(), row_factory=dict_row) as connection:
        summary = import_entries(connection, entries, commit=args.commit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
