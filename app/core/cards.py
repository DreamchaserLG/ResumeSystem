"""Scoped list projection and AND-filtering for the resume workspace."""
from datetime import datetime
from typing import Literal
import re
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.core.extraction import norm
from app.core.recruitment import stage
from app.core.screening import SearchInput, VISIBLE, _like, _months, _terms, _trusted

SCHOOL_FILTERS = ("is_985", "is_211", "is_double_first_class", "study_abroad", "qs_top100")

# Each domain tag requires a term in the approved text of a work or project record.
DOMAINS = {
    "legal": ("法律", "法务", "律师", "诉讼", "合规审查", "law firm"),
    "finance": ("金融", "银行", "证券", "投资分析", "基金", "finance"),
    "consumer": ("生活服务", "本地生活", "餐饮", "零售", "消费品", "retail"),
    "technology": ("软件开发", "后端", "前端", "微服务", "算法", "software"),
    "healthcare": ("医疗", "医院", "制药", "临床", "healthcare"),
    "human_resources": ("人力资源", "人事运营", "校园招聘", "社会招聘"),
    "biomed": ("生物医药", "生物工艺", "药物研发", "药效评价"),
}


class CardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int | None = Field(default=None, gt=0)
    department_id: int | None = Field(default=None, gt=0)
    parse_status: Literal["failed","parsing"] | None = None
    stalled: bool = False
    stage_key: str | None = Field(default=None, pattern=r"^(review|progress|rejected|hired|interview:[1-9][0-9]{0,17})$")
    query: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)
    page: int = Field(default=1, ge=1, le=10000)
    size: int = Field(default=12, ge=1, le=100)


def education_summary(session, version, policy):
    if version is None:
        return []
    return [dict(row) for row in session.execute(text(f"""
        WITH visible AS (
            SELECT field_key FROM field_usage_policy JOIN field_definition USING(field_key)
            WHERE purpose='preview' AND policy_version=:policy AND allowed AND status='active'
        )
        , ranked AS (
        SELECT e.record_no,coalesce(s.canonical_name,e.school_raw) AS school,
               CASE WHEN EXISTS (SELECT 1 FROM visible WHERE field_key='education.degree_level')
                   AND {_trusted("e","education.degree_level")} THEN e.degree_level END AS degree,
               CASE WHEN EXISTS (SELECT 1 FROM visible WHERE field_key='education.school_tags') THEN
                   ARRAY(SELECT tag_code FROM school_tag t WHERE t.school_id=s.id AND t.scope='school'
                         AND tag_code IN ('is_985','is_211','is_double_first_class')
                         GROUP BY tag_code ORDER BY array_position(ARRAY['is_985','is_211','is_double_first_class'],tag_code))
                   || CASE WHEN s.location NOT IN ('China (Mainland)','Hong Kong SAR, China','Macao SAR, China','Taiwan')
                           THEN ARRAY['study_abroad'] ELSE ARRAY[]::text[] END ELSE ARRAY[]::text[] END AS tags,
               rank.rank AS qs_rank,rank.edition AS qs_edition,
               row_number() OVER (ORDER BY rank.rank NULLS LAST,e.record_no) AS qs_order
        FROM resume_education e LEFT JOIN school s ON s.id=e.school_id
        LEFT JOIN school_world_rank rank ON rank.school_id=s.id
            AND rank.edition=(SELECT max(edition) FROM school_world_rank)
            AND EXISTS (SELECT 1 FROM visible WHERE field_key='education.school_tags')
        WHERE e.version_id=:version AND {_trusted("e","education.school_raw")}
          AND EXISTS (SELECT 1 FROM visible WHERE field_key='education.school_raw')
        )
        SELECT record_no,school,degree,tags,
               CASE WHEN qs_order=1 THEN qs_rank END AS qs_rank,
               CASE WHEN qs_order=1 THEN qs_edition END AS qs_edition
        FROM ranked ORDER BY record_no
    """), {"version":version,"policy":policy}).mappings()]


def cards(session, auth, request):
    session.execute(text("SET LOCAL jit=off"))
    policy = session.execute(text("SELECT policy_version FROM search_state WHERE singleton")).scalar_one()
    allowed = set(session.execute(text("""
        SELECT p.field_key,p.purpose FROM field_usage_policy p JOIN field_definition f USING(field_key)
        WHERE p.policy_version=:p AND p.allowed AND f.status='active'
    """), {"p": policy}).all())
    field_map = {"degree":"education.degree_level", "school":"education.school_tags",
                 "experience":"work.period", "domain":"experience.domain", "skill":"skill.name",
                 "capability":"skill.name", "language":"skill.name"}
    # Old clients may still send numbered QS tags; they now all mean "has a QS rank".
    selected = list(dict.fromkeys(
        "school:qs_top100" if re.fullmatch(r"school:qs-(?:[1-9][0-9]?|100)", item) else item
        for item in request.tags
    ))
    for item in selected:
        category, _, value = item.partition(":")
        if not value or len(value) > 80 or category not in field_map:
            raise HTTPException(422, "无效的简历标签")
        if (field_map[category], "filter") not in allowed:
            raise HTTPException(403, "标签筛选未获授权")
        if category == "school" and value not in SCHOOL_FILTERS:
            raise HTTPException(422, "无效的院校标签")

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    params = {"deps": auth.departments, "job": request.job_id, "department": request.department_id,
              "parse_status": request.parse_status, "policy": policy,
              "size": request.size, "offset": (request.page-1)*request.size, "tags": selected,
              "end_month": today.year*12+today.month}
    preview = lambda key: (key, "preview") in allowed
    field = lambda key, expression: expression if preview(key) else "NULL::text"
    education = f"""ARRAY(SELECT e.degree_level FROM resume_education e
        WHERE e.version_id=a.version_id AND e.degree_level IS NOT NULL AND {_trusted("e","education.degree_level")}
        ORDER BY array_position(ARRAY['phd','master','bachelor','associate','high_school','other'],e.degree_level) LIMIT 1)"""
    school = f"""ARRAY(
        SELECT flags.tag FROM (
            SELECT DISTINCT tag.tag FROM resume_education e JOIN school s ON s.id=e.school_id
            CROSS JOIN LATERAL (
                SELECT t.tag_code AS tag FROM school_tag t WHERE t.school_id=s.id AND t.scope='school'
                    AND t.tag_code IN ('is_985','is_211','is_double_first_class')
                UNION ALL SELECT 'study_abroad'
                    WHERE s.location NOT IN ('China (Mainland)','Hong Kong SAR, China','Macao SAR, China','Taiwan')
            ) tag WHERE e.version_id=a.version_id AND {_trusted("e","education.school_raw")}
        ) flags ORDER BY CASE flags.tag WHEN 'is_985' THEN 1 WHEN 'is_211' THEN 2
            WHEN 'is_double_first_class' THEN 3 ELSE 4 END)"""
    best_qs_rank = f"""(SELECT min(rank.rank) FROM resume_education e
        JOIN school_world_rank rank ON rank.school_id=e.school_id
        WHERE e.version_id=a.version_id AND rank.edition=(SELECT max(edition) FROM school_world_rank)
          AND {_trusted("e","education.school_raw")})"""
    trusted_skill = _trusted("k","skill.name")
    skills = f"""ARRAY(SELECT DISTINCT sk.name FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id
        WHERE k.version_id=a.version_id AND {trusted_skill} ORDER BY sk.name)"""
    capabilities = f"""ARRAY(SELECT cg.code FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id
        JOIN capability_group cg ON cg.code=sk.capability_code
        WHERE k.version_id=a.version_id AND {trusted_skill}
        GROUP BY cg.code,cg.display_order ORDER BY count(DISTINCT coalesce(k.parent_record_no,0)) DESC,
          count(DISTINCT k.skill_id) DESC,cg.display_order,cg.code)"""
    main_language = f"""(SELECT sk.name FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id
        WHERE k.version_id=a.version_id AND sk.is_programming_language AND {trusted_skill}
        GROUP BY sk.name ORDER BY min(coalesce(k.language_priority,3)),
          CASE WHEN min(coalesce(k.language_priority,3)) <= 2
            THEN min(k.record_no) FILTER (WHERE k.language_priority <= 2) END,
          count(DISTINCT k.parent_record_no) DESC,min(k.record_no),sk.name LIMIT 1)"""
    capability_names = dict(session.execute(text("SELECT code,name FROM capability_group")).all()) if preview("skill.name") else {}
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
             {best_qs_rank if preview("education.school_tags") else "NULL::smallint"} AS qs_rank,
             {capabilities if preview("skill.name") else "ARRAY[]::text[]"} AS capabilities,
             {main_language if preview("skill.name") else "NULL::text"} AS main_language,
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
             CASE WHEN q.qs_rank IS NOT NULL THEN ARRAY['school:qs_top100'] ELSE ARRAY[]::text[] END ||
             ARRAY(SELECT 'skill:'||lower(x) FROM unnest(q.skills) x) ||
             ARRAY(SELECT 'capability:'||x FROM unnest(q.capabilities) x) ||
             CASE WHEN q.main_language IS NOT NULL THEN ARRAY['language:'||lower(q.main_language)] ELSE ARRAY[]::text[] END ||
             ARRAY(SELECT 'domain:'||x FROM unnest(q.domains) x) ||
             CASE WHEN q.work_months IS NULL THEN ARRAY[]::text[] ELSE ARRAY['experience:'||
               CASE WHEN q.work_months<12 THEN 'under1' WHEN q.work_months<36 THEN '1to3'
                    WHEN q.work_months<60 THEN '3to5' ELSE '5plus' END] END)""")
    stage_filter = ""
    if request.stage_key:
        code, _, round_id = request.stage_key.partition(":")
        params["stage"] = code
        stage_filter = (" AND a.recruitment_stage IN ('progress','interview')" if code == "progress"
                        else " AND a.recruitment_stage=:stage")
        if round_id:
            params["round_id"] = int(round_id)
            stage_filter += " AND a.interview_round_id=:round_id"
    scoped = f"""
        SELECT a.id,a.active_version_id AS version_id,a.job_id,j.title AS job_title,a.status,a.submitted_at,
               a.pending_version_id,v.status AS parse_status,a.workflow_version,a.recruitment_stage,a.interview_round_id,ir.name AS interview_round_name
        FROM application a JOIN job j ON j.id=a.job_id
        LEFT JOIN job_interview_round ir ON ir.id=a.interview_round_id
        LEFT JOIN resume_version v ON v.id=coalesce(a.pending_version_id,a.active_version_id,a.submitted_version_id)
        WHERE {VISIBLE} AND (CAST(:job AS bigint) IS NULL OR a.job_id=:job)
          AND (CAST(:department AS bigint) IS NULL OR a.handling_department_id=:department)
          AND (CAST(:parse_status AS text) IS NULL OR v.status=:parse_status) {stage_filter}
          {"AND recruitment_stalled(a.id)" if request.stalled else ""}
    """
    sql = "WITH scoped AS (" + scoped + ")"
    source = "scoped"
    if where:
        categories = {tag.split(":", 1)[0] for tag in selected}
        features = {
            "degrees": education if "degree" in categories else "ARRAY[]::text[]",
            "schools": school if any(tag.startswith("school:") and tag != "school:qs_top100" for tag in selected) else "ARRAY[]::text[]",
            "qs_rank": best_qs_rank if "school:qs_top100" in selected else "NULL::smallint",
            "skills": skills if "skill" in categories else "ARRAY[]::text[]",
            "capabilities": capabilities if "capability" in categories else "ARRAY[]::text[]",
            "main_language": main_language if "language" in categories else "NULL::text",
            "domains": domains if "domain" in categories else "ARRAY[]::text[]",
            "work_months": f"({_months('a',lower_bound=True)})" if "experience" in categories else "NULL::bigint",
        }
        projection = ",".join(f"{value} AS {key}" for key, value in features.items())
        sql += f""", conditions AS (
            SELECT a.*,p.name,p.target_role,{projection}
            FROM scoped a LEFT JOIN resume_profile p ON p.version_id=a.version_id
        ), matched AS (
            SELECT q.id,q.version_id,q.job_id,q.job_title,q.status,q.submitted_at,q.pending_version_id,q.parse_status,
                   q.workflow_version,q.recruitment_stage,q.interview_round_id,q.interview_round_name
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
        item["stage"] = stage(item.pop("recruitment_stage"), item.pop("interview_round_id"), item.pop("interview_round_name"))
        item["page_count"] = item["page_count"] or int(item["legacy_preview"])
        item["tags"] = []
        for category, column in (("degree","degrees"),("school","schools"),("domain","domains")):
            if preview(field_map[category]):
                for value in item[column]:
                    item["tags"].append({"id":f"{category}:{value}",
                                         "type":category,"value":value,"filterable": (field_map[category],"filter") in allowed})
            item.pop(column, None)
        qs_rank = item.pop("qs_rank")
        if qs_rank is not None:
            item["tags"].append({"id":"school:qs_top100","type":"school","value":f"qs-{qs_rank}",
                                 "filterable":("education.school_tags","filter") in allowed})
        language = item.pop("main_language")
        groups = item.pop("capabilities")
        if language:
            item["tags"].append({"id":f"language:{norm(language)}","type":"language","value":language,
                                 "filterable":("skill.name","filter") in allowed})
        for group in groups[:4]:
            item["tags"].append({"id":f"capability:{group}","type":"capability","value":capability_names[group],
                                 "filterable":("skill.name","filter") in allowed})
        months = item.pop("work_months")
        if preview("work.period") and months is not None:
            bucket = "under1" if months < 12 else "1to3" if months < 36 else "3to5" if months < 60 else "5plus"
            item["tags"].append({"id":f"experience:{bucket}","type":"experience","value":str(months),
                                 "filterable":("work.period","filter") in allowed})
        response.append(item)
    return {"items":response,"total":total,"page":request.page,"size":request.size,
            "school_filters": ("education.school_tags", "filter") in allowed}
