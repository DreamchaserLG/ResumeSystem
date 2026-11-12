"""Scoped list projection and AND-filtering for the resume workspace."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.core.extraction import norm
from app.core.screening import SearchInput, VISIBLE, _like, _months, _terms, _trusted

# Each domain tag requires a term in the approved text of a work or project record.
DOMAINS = {
    "legal": ("法律", "法务", "律师", "诉讼", "合规审查", "law firm"),
    "finance": ("金融", "银行", "证券", "投资分析", "基金", "finance"),
    "consumer": ("生活服务", "本地生活", "餐饮", "零售", "消费品", "retail"),
    "technology": ("软件开发", "后端", "前端", "微服务", "算法", "software"),
    "healthcare": ("医疗", "医院", "制药", "临床", "healthcare"),
}


class CardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int | None = Field(default=None, gt=0)
    query: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)
    page: int = Field(default=1, ge=1, le=10000)
    size: int = Field(default=12, ge=1, le=100)


def cards(session, auth, request):
    session.execute(text("SET LOCAL jit=off"))
    policy = session.execute(text("SELECT policy_version FROM search_state WHERE singleton")).scalar_one()
    allowed = set(session.execute(text("""
        SELECT p.field_key,p.purpose FROM field_usage_policy p JOIN field_definition f USING(field_key)
        WHERE p.policy_version=:p AND p.allowed AND f.status='active'
    """), {"p": policy}).all())
    field_map = {"degree":"education.degree_level", "school":"education.school_tags",
                 "experience":"work.period", "domain":"experience.domain", "skill":"skill.name"}
    selected = list(dict.fromkeys(request.tags))
    for item in selected:
        category, _, value = item.partition(":")
        if not value or len(value) > 80 or category not in field_map:
            raise HTTPException(422, "无效的简历标签")
        if (field_map[category], "filter") not in allowed:
            raise HTTPException(403, "标签筛选未获授权")
        if category == "school" and (not request.job_id or not session.execute(text("""
            SELECT 1 FROM job_school_filter WHERE job_id=:j AND tag_code=:tag
        """), {"j": request.job_id, "tag": value}).first()):
            raise HTTPException(403, "请先选择获准使用该院校标签的岗位")

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    params = {"deps": auth.departments, "job": request.job_id, "policy": policy,
              "size": request.size, "offset": (request.page-1)*request.size, "tags": selected,
              "end_month": today.year*12+today.month}
    preview = lambda key: (key, "preview") in allowed
    field = lambda key, expression: expression if preview(key) else "NULL::text"
    education = "ARRAY(SELECT DISTINCT e.degree_level FROM resume_education e WHERE e.version_id=a.version_id AND e.degree_level IS NOT NULL AND " + _trusted("e","education.degree_level") + " ORDER BY e.degree_level)"
    school = "ARRAY(SELECT DISTINCT t.tag_code FROM resume_education e JOIN school_tag t USING(school_id) WHERE e.version_id=a.version_id AND t.scope='school' AND " + _trusted("e","education.school_raw") + " ORDER BY t.tag_code)"
    skills = "ARRAY(SELECT DISTINCT sk.name FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id WHERE k.version_id=a.version_id AND " + _trusted("k","skill.name") + " ORDER BY sk.name)"
    for key in DOMAINS:
        params[f"domain_{key}"] = [_like(word) for word in DOMAINS[key]]
    domain_values = ",".join(f"('{key}',CAST(:domain_{key} AS text[]))" for key in DOMAINS)
    domains = f"""ARRAY(SELECT d.tag FROM (VALUES {domain_values}) d(tag,patterns) WHERE EXISTS (
        SELECT 1 FROM resume_search_record p JOIN resume_record r USING(version_id,record_no)
        WHERE p.version_id=a.version_id AND p.policy_version=:policy AND r.kind IN ('work','project')
        AND p.text_norm LIKE ANY(d.patterns)) ORDER BY d.tag)"""

    metadata = f"""
      SELECT a.*, {field("profile.name", "p.name")} AS name,
             {field("profile.city", "p.city")} AS city,
             {field("profile.target_role", "p.target_role")} AS target_role,
             {field("profile.gender", "private.gender")} AS gender,
             {field("profile.address", "private.address")} AS address,
             {field("contact.phone", "(SELECT c.value FROM resume_contact c WHERE c.version_id=a.version_id AND c.type='phone' AND NOT EXISTS (SELECT 1 FROM resume_field_issue i WHERE i.version_id=c.version_id AND i.record_no=c.record_no AND i.field_key='contact.phone') ORDER BY c.record_no LIMIT 1)")} AS phone,
             {"v.avatar_key IS NOT NULL" if preview("profile.avatar") else "false"} AS has_avatar,
             (SELECT count(*) FROM resume_preview_page pp WHERE pp.version_id=a.version_id) AS page_count,
             v.preview_key IS NOT NULL AS legacy_preview,
             {education if preview("education.degree_level") or ("education.degree_level","filter") in allowed else "ARRAY[]::text[]"} AS degrees,
             {school if preview("education.school_tags") or ("education.school_tags","filter") in allowed else "ARRAY[]::text[]"} AS schools,
             {skills if preview("skill.name") or ("skill.name","filter") in allowed else "ARRAY[]::text[]"} AS skills,
             {domains if preview("experience.domain") or ("experience.domain","filter") in allowed else "ARRAY[]::text[]"} AS domains,
             {f"({_months('a',lower_bound=True)})" if preview("work.period") or ("work.period","filter") in allowed else "NULL::bigint"} AS work_months
      FROM {{source}} a
      LEFT JOIN resume_version v ON v.id=a.version_id
      LEFT JOIN resume_profile p ON p.version_id=a.version_id
      LEFT JOIN resume_card_private private ON private.version_id=a.version_id
    """
    words = re.split(r"[\s,，;；]+", request.query.strip()) if request.query.strip() else []
    if len(words) > 8:
        raise HTTPException(422, "最多可输入 8 个关键词")
    _, groups = _terms(session, SearchInput(keywords=words)) if words else (0, [])
    where = []
    for index, (word, terms) in enumerate(groups):
        params[f"q{index}"] = [_like(term) for term in terms]
        params[f"exact{index}"] = _like(word)
        name = f"lower(q.name) LIKE :exact{index} ESCAPE '!' OR" if ("profile.name","search") in allowed else ""
        target = f"lower(q.target_role) LIKE :exact{index} ESCAPE '!' OR" if ("profile.target_role","search") in allowed else ""
        where.append(f"""({name} {target} lower(q.job_title) LIKE :exact{index} ESCAPE '!' OR EXISTS (
            SELECT 1 FROM resume_search_record sr WHERE sr.version_id=q.version_id AND sr.policy_version=:policy
              AND EXISTS (SELECT 1 FROM unnest(CAST(:q{index} AS text[])) t WHERE sr.text_norm LIKE t ESCAPE '!')
        ))""")
    if selected:
        where.append("""CAST(:tags AS text[]) <@
            (ARRAY(SELECT 'degree:'||x FROM unnest(q.degrees) x) ||
             ARRAY(SELECT 'school:'||x FROM unnest(q.schools) x) ||
             ARRAY(SELECT 'skill:'||lower(x) FROM unnest(q.skills) x) ||
             ARRAY(SELECT 'domain:'||x FROM unnest(q.domains) x) ||
             CASE WHEN q.work_months IS NULL THEN ARRAY[]::text[] ELSE ARRAY['experience:'||
               CASE WHEN q.work_months<12 THEN 'under1' WHEN q.work_months<36 THEN '1to3'
                    WHEN q.work_months<60 THEN '3to5' ELSE '5plus' END] END)""")
    scoped = f"""
        SELECT a.id,a.active_version_id AS version_id,a.job_id,j.title AS job_title,a.status,a.submitted_at,
               a.pending_version_id,v.status AS parse_status
        FROM application a JOIN job j ON j.id=a.job_id
        LEFT JOIN resume_version v ON v.id=coalesce(a.pending_version_id,a.active_version_id,a.submitted_version_id)
        WHERE {VISIBLE} AND (CAST(:job AS bigint) IS NULL OR a.job_id=:job)
    """
    sql = "WITH scoped AS (" + scoped + ")"
    source = "scoped"
    if where:
        categories = {tag.split(":", 1)[0] for tag in selected}
        features = {
            "degrees": education if "degree" in categories else "ARRAY[]::text[]",
            "schools": school if "school" in categories else "ARRAY[]::text[]",
            "skills": skills if "skill" in categories else "ARRAY[]::text[]",
            "domains": domains if "domain" in categories else "ARRAY[]::text[]",
            "work_months": f"({_months('a',lower_bound=True)})" if "experience" in categories else "NULL::bigint",
        }
        projection = ",".join(f"{value} AS {key}" for key, value in features.items())
        sql += f""", conditions AS (
            SELECT a.*,p.name,p.target_role,{projection}
            FROM scoped a LEFT JOIN resume_profile p ON p.version_id=a.version_id
        ), matched AS (
            SELECT q.id,q.version_id,q.job_id,q.job_title,q.status,q.submitted_at,q.pending_version_id,q.parse_status
            FROM conditions q WHERE {" AND ".join(where)}
        )"""
        source = "matched"
    sql += f", slice AS (SELECT *,count(*) OVER() AS total FROM {source} ORDER BY id DESC LIMIT :size OFFSET :offset)"
    sql += metadata.replace("{source}","slice") + " ORDER BY a.id DESC"
    rows = session.execute(text(sql), params).mappings().all()
    total = rows[0]["total"] if rows else 0
    if not rows and request.page > 1:
        total = cards(session, auth, request.model_copy(update={"page": 1, "size": 1}))["total"]
    response = []
    for row in rows:
        item = dict(row)
        item.pop("total", None)
        item["page_count"] = item["page_count"] or int(item["legacy_preview"])
        item["tags"] = []
        for category, column in (("degree","degrees"),("school","schools"),("domain","domains"),("skill","skills")):
            if preview(field_map[category]):
                for value in (item[column][:5] if category=="skill" else item[column]):
                    item["tags"].append({"id":f"{category}:{norm(value) if category=='skill' else value}",
                                         "type":category,"value":value,"filterable": (field_map[category],"filter") in allowed})
            item.pop(column, None)
        months = item.pop("work_months")
        if preview("work.period") and months is not None:
            bucket = "under1" if months < 12 else "1to3" if months < 36 else "3to5" if months < 60 else "5plus"
            item["tags"].append({"id":f"experience:{bucket}","type":"experience","value":str(months),
                                 "filterable":("work.period","filter") in allowed})
        response.append(item)
    return {"items":response,"total":total,"page":request.page,"size":request.size}
