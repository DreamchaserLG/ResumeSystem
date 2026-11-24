"""Verify and export the complete current job directory from PostgreSQL to PDF."""
import argparse
from collections import Counter
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unicodedata import normalize
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from migrations.migrate import get_dsn
from scripts.import_recruitment_directory import parse_description

JOB_COLUMNS = {
    "id": "岗位 ID", "title": "岗位名称", "department_id": "部门 ID", "source_category": "来源分类",
    "jd": "岗位职责", "requirements": "任职要求", "status": "岗位状态", "recruiter_user_id": "负责人账号 ID",
    "recruiter_name": "手填负责人", "recruitment_type": "招聘类型", "work_location": "工作地点",
    "employment_type": "职位性质", "planned_headcount": "计划人数", "target_date": "到岗目标日",
    "recruitment_start": "招聘开始日", "recruitment_end": "招聘截止日", "required_skills": "必备技能",
    "experience_tags": "经历标签", "created_at": "创建时间", "updated_at": "更新时间", "hidden": "目录可见性",
}
STATUS = {"open": "在招", "paused": "暂停", "closed": "关闭"}
TYPES = {"social": "社招", "campus": "校招", "internship": "实习", "internal": "内部招聘"}
SQL = """
    SELECT j.*, d.name AS department,d.status AS department_status,c.id AS company_id,c.name AS company,
        u.display_name AS recruiter_account,u.status AS recruiter_status,
        ref.category AS reference_category,ref.sources AS reference_sources,ref.researched_on AS reference_date,
        (ref.jd_snapshot=j.jd AND ref.requirements_snapshot=j.requirements) IS TRUE AS reference_current,
        (ref.reference_recruitment_start=j.recruitment_start
         AND ref.reference_recruitment_end=j.recruitment_end) IS TRUE AS window_reference,
        ref.reference_recruitment_start AS reference_start,ref.reference_recruitment_end AS reference_end,
        (SELECT count(*) FROM application a WHERE a.job_id=j.id AND a.deleted_at IS NULL) AS applications,
        coalesce((SELECT jsonb_agg(jsonb_build_object('name',r.name,'position',r.position,'id',r.id) ORDER BY r.position)
            FROM job_interview_round r WHERE r.job_id=j.id),'[]'::jsonb) AS rounds,
        coalesce((SELECT jsonb_agg(jsonb_build_object('system',m.source_system,'code',m.source_job_id,
            'snapshot',m.posting_snapshot) ORDER BY m.source_system,m.source_job_id)
            FROM job_source_mapping m WHERE m.job_id=j.id),'[]'::jsonb) AS source_records
    FROM job j LEFT JOIN department d ON d.id=j.department_id
    LEFT JOIN recruiting_company c ON c.id=d.company_id
    LEFT JOIN app_user u ON u.id=j.recruiter_user_id
    LEFT JOIN job_posting_reference ref ON ref.job_id=j.id
    ORDER BY d.name,j.id
"""
JOINED_FIELDS = {
    "department", "department_status", "company_id", "company", "recruiter_account", "recruiter_status",
    "reference_category", "reference_sources", "reference_date", "reference_current", "window_reference",
    "reference_start", "reference_end", "applications", "rounds", "source_records",
}
CHECKS = {
    "company": "所属公司", "department": "所属部门", "title": "岗位名称", "status": "岗位状态",
    "jd": "岗位职责", "requirements": "任职要求", "recruiter_account": "负责人账号",
    "planned_headcount": "计划人数", "rounds": "招聘流程", "recruitment_start": "招聘开始日",
    "recruitment_end": "招聘截止日", "target_date": "到岗目标日", "required_skills": "必备技能",
    "experience_tags": "经历标签", "work_location": "工作地点", "source_records": "来源编码关联",
}


def has_value(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def fetch_and_verify():
    with psycopg.connect(get_dsn(), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL TIME ZONE 'Asia/Shanghai'")
        db_columns = {row["column_name"] for row in connection.execute("""
            SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='job'
        """)}
        total = connection.execute("SELECT count(*) AS total FROM job").fetchone()["total"]
        result = connection.execute(SQL)
        query_fields = {column.name for column in result.description}
        rows = result.fetchall()
        timestamp = connection.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()["ts"]
        if db_columns != set(JOB_COLUMNS) or query_fields != db_columns | JOINED_FIELDS:
            raise ValueError(f"字段清单与检索结果不符：{sorted(db_columns ^ set(JOB_COLUMNS))}, "
                             f"{sorted(query_fields ^ (db_columns | JOINED_FIELDS))}")
        if len(rows) != total or len({row['id'] for row in rows}) != total:
            raise ValueError("PDF 检索结果与岗位表数量不符或包含重复岗位")
        if any(not all(has_value(row[key]) for key in ("id", "title", "department", "company", "status")) for row in rows):
            raise ValueError("缺少岗位身份或归属字段")
        sample = connection.execute("""
            SELECT count(*) AS total FROM job_source_mapping WHERE source_system='recruitment_export'
        """).fetchone()["total"]
        source_total = sum(source["system"] == "recruitment_export" for row in rows for source in row["source_records"])
        if source_total != sample:
            raise ValueError("来源编码关联未完整检索")
    stats = {key: sum(has_value(row[key]) for row in rows) for key in {**JOB_COLUMNS, **CHECKS}}
    print(json.dumps({"checked_before_render": True, "job_count": total, "job_fields": len(db_columns),
                      "related_fields": len(JOINED_FIELDS), "source_mappings": source_total,
                      "nonempty_counts": stats}, ensure_ascii=False), flush=True)
    return rows, timestamp, stats


def show(value):
    if not has_value(value):
        return "<span class='empty'>未填写</span>"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return escape("、".join(map(str, value)))
    return escape(str(value))


def variables(items):
    return "<dl>" + "".join(f"<div><dt>{escape(label)}</dt><dd>{show(value)}</dd></div>" for label, value in items) + "</dl>"


def current_jd_source(job):
    for source in job["source_records"]:
        snapshot = source["snapshot"]
        jd_source = snapshot.get("jd_source")
        if jd_source:
            primary = snapshot["primary"]
            from_primary = jd_source == primary["source"]
            document = primary if from_primary else snapshot["supplement"]
            if parse_description(document["title"], document["raw_jd"]) == (job["jd"], job["requirements"]):
                return "招聘清单原文" if from_primary else "补充清单原文"
    if job["reference_current"]:
        return "招聘网站参考稿"
    return "数据库现有记录" if has_value(job["jd"]) or has_value(job["requirements"]) else "未填写"


def source_name(filename):
    return filename.partition("---")[0].removesuffix(".md") if filename else None


def references(job):
    bodies = []
    for record in job["source_records"]:
        snapshot = record["snapshot"]
        primary = snapshot.get("primary", {})
        supplement = snapshot.get("supplement") or {}
        bodies.append("<h3>来源清单</h3>" + variables([
            ("来源职位编码", record["code"]), ("来源岗位名称", primary.get("title")),
            ("原清单分类", primary.get("category")), ("原清单 JD 状态", primary.get("jd_status")),
            ("主清单文件", source_name(primary.get("source"))), ("主清单日期", primary.get("exported_on")),
            ("补充清单文件", source_name(supplement.get("source"))), ("导入 JD 文件", source_name(snapshot.get("jd_source"))),
            ("来源标注经验", primary.get("experience") or supplement.get("experience")),
            ("来源标注薪资", primary.get("salary") or supplement.get("salary")),
        ]))
    if job["reference_sources"]:
        links = []
        for source in job["reference_sources"]:
            url = source.get("url", "")
            label = f"{source.get('platform', '')} · {source.get('posting', '')}"
            links.append(f"<li><a href='{escape(url)}'>{escape(label)}</a></li>" if urlsplit(url).scheme == "https"
                         else f"<li>{escape(label)}</li>")
        bodies.append(f"<h3>网站参考来源</h3><p class='subtle'>{show(job['reference_date'])} · "
                      f"{'当前采用' if job['reference_current'] else '历史来源'}</p><ul>{''.join(links)}</ul>")
    return "".join(bodies)


def job_page(job):
    recruiter = job["recruiter_account"] or job["recruiter_name"]
    information = [
        ("所属公司", f"{job['company']} · #{job['company_id']}"),
        ("所属部门", f"{job['department']} · #{job['department_id']}"),
        ("部门状态", {"active": "启用", "disabled": "停用"}.get(job["department_status"], job["department_status"])),
        ("岗位分类", job["reference_category"] or job["source_category"]), ("来源分类", job["source_category"]),
        ("负责人", recruiter), ("负责人账号 ID", job["recruiter_user_id"]), ("手填负责人", job["recruiter_name"]),
        ("工作地点", job["work_location"]), ("职位性质", job["employment_type"]),
        ("招聘类型", TYPES.get(job["recruitment_type"], job["recruitment_type"])),
        ("计划人数", job["planned_headcount"]), ("招聘开始日", job["recruitment_start"]),
        ("招聘截止日", job["recruitment_end"]), ("到岗目标日", job["target_date"]), ("关联申请数", job["applications"]),
        ("招聘流程", " → ".join(item["name"] for item in job["rounds"])),
        ("排期来源", "参考排期" if job["window_reference"] else "数据库记录" if job["recruitment_start"] or job["recruitment_end"] else None),
        ("创建时间", job["created_at"]), ("更新时间", job["updated_at"]),
        ("目录可见性", "已隐藏" if job["hidden"] else "可见"),
    ]
    return f"""<article class='job' id='job-{job['id']}'>
      <p class='eyebrow'>岗位 #{job['id']}　·　{show(STATUS.get(job['status'], job['status']))}</p>
      <h1>{show(job['title'])}</h1>{variables(information)}
      <h3>必备技能</h3><p>{show(job['required_skills'])}</p>
      <h3>经历标签</h3><p>{show(job['experience_tags'])}</p>
      <div class='jd'><p class='eyebrow'>{show(current_jd_source(job))}</p>
      <h2>岗位职责</h2><p class='description'>{show(job['jd'])}</p>
      <h2>任职要求</h2><p class='description'>{show(job['requirements'])}</p></div>
      {references(job)}</article>"""


CSS = """
@page { size: A4; margin: 15mm 16mm 17mm;
  @bottom-left { content: '招聘岗位数据库导出'; color: #566577; font: 9pt 'Noto Sans CJK SC'; }
  @bottom-right { content: counter(page) ' / ' counter(pages); color: #566577; font: 9pt 'Noto Sans CJK SC'; }
}
* { box-sizing: border-box; }
body { margin: 0; color: #182b39; font: 10pt/1.52 'Noto Sans CJK SC', sans-serif; print-color-adjust: exact; }
h1,h2,h3,p { margin: 0; } h1 { font-size: 24pt; line-height: 1.28; margin: 3px 0 13px; }
h2 { font-size: 14pt; margin: 12px 0 7px; } h3 { font-size: 10.5pt; margin: 10px 0 2px; }
h1,h2,h3,.eyebrow { break-after: avoid; }
p { margin-bottom: 3px; } .subtle,.empty { color: #596674; }
.eyebrow { color: #14614f; font-weight: 700; font-size: 9pt; }
.cover { border-top: 5px solid #14614f; padding-top: 22px; }
.metric { background: #eaf4f0; padding: 13px 15px; margin: 16px 0 8px; font-size: 13pt; font-weight: 700; }
.scope { margin: 8px 0 18px; max-width: 62ch; }
.roster,.job { break-before: page; }
dl { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 1px 16px; margin: 8px 0 10px; }
dl div { display:flex; gap: 9px; border-bottom: 1px solid #e3e9ef; padding: 2px 0; break-inside: avoid; }
dt { flex: 0 0 6.6em; color: #596674; } dd { margin: 0; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; font-size: 9pt; }
thead { display: table-header-group; background: #eaf4f0; }
th,td { border-bottom: 1px solid #e3e9ef; padding: 4px 6px; text-align:left; }
tr { break-inside: avoid; } th:first-child,td:first-child { white-space: nowrap; }
a { color: #165845; text-underline-offset: 2px; } ul { margin: 4px 0; padding-left: 22px; }
.jd { border-top: 1px solid #c7d9d3; padding-top: 9px; margin-top: 14px; }
.description { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.62; orphans: 2; widows: 2; }
"""


def report_html(rows, timestamp, stats):
    total = len(rows)
    counts = Counter(row["status"] for row in rows)
    departments = Counter(row["department"] for row in rows)
    checks = [(label, f"{stats[key]} / {total} 条有值") for key, label in CHECKS.items()]
    roster = "".join(f"<tr><td>{row['id']}</td><td><a href='#job-{row['id']}'>{show(row['title'])}</a></td>"
                     f"<td>{show(row['department'])}</td><td>{show(STATUS.get(row['status'],row['status']))}</td>"
                     f"<td>{show(row['planned_headcount'])}</td><td>{show(current_jd_source(row))}</td></tr>" for row in rows)
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>招聘岗位数据库全量导出</title>
    <style>{CSS}</style><body><section class='cover'>
    <p class='eyebrow'>数据库快照 · {show(timestamp)}（北京时间）</p><h1>招聘岗位数据库全量导出</h1>
    <div class='metric'>共 {total} 个岗位　·　{len(departments)} 个部门　·　全部岗位状态</div>
    <p class='scope'>已核对 {len(JOB_COLUMNS)} 个岗位主表字段及 {len(JOINED_FIELDS)} 个关联字段，查询行数与数据库一致。
    以下统计区分字段已检索和有值。报告按数据库实际值展示空项和 JD 来源。</p>
    <h2>部门与状态</h2>{variables([*departments.items(), *((STATUS.get(key,key), counts[key]) for key in STATUS)])}
    <h2>导出前字段核验</h2>{variables(checks)}</section>
    <section class='roster'><h1>全部岗位一览</h1>
    <table><thead><tr><th>ID</th><th>岗位名称</th><th>所属部门</th><th>状态</th><th>计划人数</th><th>当前 JD</th></tr></thead>
    <tbody>{roster}</tbody></table></section>{''.join(job_page(row) for row in rows)}</body></html>"""


def normalized_text(value):
    return re.sub(r"\s+", "", normalize("NFKC", value))


def print_and_verify(rows, timestamp, stats, output, chrome):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="job-directory-", dir=output.parent) as directory:
        tmp = Path(directory)
        document = tmp / "jobs.html"
        document.write_text(report_html(rows, timestamp, stats), encoding="utf-8")
        pdf = tmp / "jobs.pdf"
        subprocess.run([chrome, "--headless", "--no-pdf-header-footer", "--disable-background-networking",
                        f"--user-data-dir={tmp / 'chrome'}", f"--print-to-pdf={pdf}", document.as_uri()],
                       check=True, capture_output=True, timeout=90)
        extracted = subprocess.check_output(["pdftotext", "-raw", str(pdf), "-"], text=True)
        extracted = re.sub(r"(?m)^(?:\d+/\d+ 招聘岗位数据库导出|招聘岗位数据库导出 \d+/\d+)\s*$",
                           "", extracted.replace("\f", "\n"))
        actual = list(re.finditer(r"岗位\s*#\s*(\d+)", extracted))
        if len(actual) != len(rows) or [int(match[1]) for match in actual] != [row["id"] for row in rows]:
            raise ValueError("PDF 文本回读未包含逐条岗位记录")
        for index, row in enumerate(rows):
            end = actual[index + 1].start() if index + 1 < len(actual) else len(extracted)
            job_text = normalized_text(extracted[actual[index].end():end])
            for field in ("title", "department", "jd", "requirements"):
                value = row[field]
                if has_value(value) and normalized_text(value) not in job_text:
                    raise ValueError(f"PDF 文本回读与岗位 {row['id']} 的字段 {field} 不一致")
        pdf.chmod(0o600)
        pdf.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chrome", default=shutil.which("google-chrome-stable") or shutil.which("chromium"))
    args = parser.parse_args()
    if not args.chrome or not shutil.which("pdftotext"):
        parser.error("需要 Chrome/Chromium 与 pdftotext")
    rows, timestamp, stats = fetch_and_verify()
    print_and_verify(rows, timestamp, stats, args.output.resolve(), args.chrome)
    print(f"PDF 字段回读通过：{args.output}")


if __name__ == "__main__":
    main()
