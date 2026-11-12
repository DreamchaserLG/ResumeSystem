"""M6 structured screening and per-experience keyword ranking."""
from datetime import date
import hashlib
import json
from secrets import token_hex
from threading import BoundedSemaphore
from time import sleep
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.cache import get_redis
from app.core.extraction import norm

Status = Literal["match", "review", "reject", "no_text"]
LEVELS = ("high_school", "associate", "bachelor", "master", "phd")
LIMIT_FALLBACK = BoundedSemaphore(4)
UNLOCK = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end"
VISIBLE = "a.deleted_at IS NULL AND a.handling_department_id=ANY(CAST(:deps AS bigint[]))"


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int | None = Field(default=None, gt=0)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    section: Literal["work", "internship", "project", "education", "summary"] | None = None
    degree: Literal["high_school", "associate", "bachelor", "master", "phd"] | None = None
    skill: str | None = Field(default=None, max_length=80)
    company: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    school_tag: str | None = Field(default=None, max_length=80)
    min_months: int | None = Field(default=None, gt=0, le=720)
    same_experience: bool = True
    as_of: date = Field(default_factory=date.today)
    status: Status = "match"
    limit: int = Field(default=20, gt=0, le=100)
    offset: int = Field(default=0, ge=0, le=5000)


def _trusted(alias, key):
    return f"""
      EXISTS (SELECT 1 FROM resume_evidence_link e JOIN resume_source_span s USING(version_id,span_no)
        WHERE e.version_id={alias}.version_id AND e.record_no={alias}.record_no
          AND e.field_key='{key}' AND NOT s.is_sensitive AND s.span_kind <> 'photo'
          AND e.start_offset >= 0 AND e.end_offset > e.start_offset AND e.end_offset <= char_length(s.raw_text))
      AND NOT EXISTS (SELECT 1 FROM resume_field_issue i WHERE i.version_id={alias}.version_id
        AND i.record_no={alias}.record_no AND i.field_key='{key}')
    """


def _terms(session, inputs):
    words = list(dict.fromkeys(norm(value) for value in inputs.keywords if norm(value)))
    if any(len(word) > 100 for word in words):
        raise HTTPException(422, "单个关键词最多 100 字符")
    versions = session.execute(text("SELECT coalesce(max(dict_version),0) FROM search_term_group WHERE enabled")).scalar_one()
    groups = []
    for word in words:
        synonyms = session.execute(text("""
            SELECT DISTINCT t.term_norm FROM search_term_group g JOIN search_term t ON t.group_id=g.id
            WHERE g.enabled AND g.dict_version=:version AND (g.section_type IS NULL OR
                g.section_type=CAST(:section AS text) OR CAST(:section AS text) IS NULL)
              AND EXISTS (SELECT 1 FROM search_term orig WHERE orig.group_id=g.id AND orig.term_norm=:word)
            ORDER BY t.term_norm LIMIT 40
        """), {"version": versions, "section": inputs.section, "word": word}).scalars().all()
        groups.append((word, [word, *[term for term in synonyms if term != word]]))
    return versions, groups


def _like(value):
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _months(alias, *, lower_bound=False):
    trusted = _trusted("w", "work.period")
    result = "(SELECT sum(upper(r)-lower(r)) FROM unnest(low) r)" if lower_bound else f"""CASE
          WHEN coalesce((SELECT sum(upper(r)-lower(r)) FROM unnest(low) r),0) >= :months THEN 1
          WHEN total>0 AND incomplete=0 AND NOT EXISTS (
            SELECT 1 FROM resume_field_issue i WHERE i.version_id={alias}.version_id
            AND i.field_key IN ('work.period','work.company'))
            AND coalesce((SELECT sum(upper(r)-lower(r)) FROM unnest(high) r),0) < :months THEN -1
          ELSE 0 END"""
    return f"""
        WITH intervals AS (
            SELECT
                CASE WHEN {trusted} THEN COALESCE(
                    extract(year FROM w.start_date)::int*12+extract(month FROM w.start_date)::int-1,
                    CASE WHEN w.start_period_raw ~ '^[0-9]{{4}}$' THEN w.start_period_raw::int*12 END) END AS s,
                CASE WHEN {trusted} THEN CASE WHEN w.is_current THEN :end_month ELSE COALESCE(
                    extract(year FROM w.end_date)::int*12+extract(month FROM w.end_date)::int,
                    CASE WHEN w.end_period_raw ~ '^[0-9]{{4}}$' THEN w.end_period_raw::int*12+12 END) END END AS e,
                CASE WHEN w.start_date IS NULL THEN 11 ELSE 0 END AS start_uncertainty,
                CASE WHEN w.end_date IS NULL AND NOT w.is_current THEN 11 ELSE 0 END AS end_uncertainty
            FROM resume_work w
            WHERE w.version_id={alias}.version_id AND w.employment_type IS DISTINCT FROM 'internship'
        ), totals AS (
            SELECT range_agg(int4range(s+start_uncertainty, least(e-end_uncertainty,:end_month)))
                       FILTER (WHERE s+start_uncertainty < least(e-end_uncertainty,:end_month) AND s IS NOT NULL AND e IS NOT NULL) AS low,
                   range_agg(int4range(s,least(e,:end_month)))
                       FILTER (WHERE s < least(e,:end_month) AND s IS NOT NULL AND e IS NOT NULL) AS high,
                   count(*) AS total, count(*) FILTER (WHERE s IS NULL OR e IS NULL) AS incomplete
            FROM intervals
        )
        SELECT {result} FROM totals
    """


def _check_permissions(session, inputs, policy):
    asked = {key for key, value in (
        ("education.degree_level", inputs.degree), ("skill.name", inputs.skill),
        ("work.company", inputs.company), ("profile.city", inputs.city),
        ("education.school_tags", inputs.school_tag), ("work.period", inputs.min_months)
    ) if value is not None}
    if asked:
        allowed = set(session.execute(text("""
            SELECT p.field_key FROM field_usage_policy p JOIN field_definition f USING(field_key)
            WHERE p.policy_version=:p AND p.purpose='filter' AND p.allowed AND f.status='active'
        """), {"p": policy}).scalars())
        if not asked <= allowed:
            raise HTTPException(403, "有筛选字段未获授权")
    if inputs.school_tag and (not inputs.job_id or not session.execute(text("""
        SELECT 1 FROM job_school_filter WHERE job_id=:j AND tag_code=:tag
    """), {"j": inputs.job_id, "tag": inputs.school_tag}).first()):
        raise HTTPException(403, "此岗位未批准该院校标签筛选条件")


def _rank(session, auth, inputs, policy, groups):
    # RLS expands the correlated checks. For short interactive queries compilation dominates execution.
    session.execute(text("SET LOCAL jit=off"))
    params = {
        "deps": auth.departments, "job": inputs.job_id, "policy": policy, "target": inputs.status,
        "limit": inputs.limit, "offset": inputs.offset, "section": inputs.section,
    }
    criteria = []
    experience = []
    if inputs.degree:
        params["degree"] = LEVELS.index(inputs.degree) + 1
        ranks = "array_position(ARRAY['high_school','associate','bachelor','master','phd'],e.degree_level)"
        trust = _trusted("e", "education.degree_level")
        criteria.append(f"""CASE WHEN EXISTS (SELECT 1 FROM resume_education e WHERE e.version_id=a.version_id
                       AND {ranks} >= :degree AND {trust}) THEN 1
             WHEN EXISTS (SELECT 1 FROM resume_education e WHERE e.version_id=a.version_id
                       AND {ranks} < :degree AND {trust})
                  AND NOT EXISTS (SELECT 1 FROM resume_field_issue i WHERE i.version_id=a.version_id
                      AND i.field_key IN ('education.degree_level','education.school_raw'))
                  AND NOT EXISTS (SELECT 1 FROM resume_education e WHERE e.version_id=a.version_id
                      AND ({ranks} IS NULL OR NOT ({trust}))) THEN -1 ELSE 0 END""")
    if inputs.city:
        params["city"] = norm(inputs.city)
        trust = _trusted("p", "profile.city")
        criteria.append(f"""coalesce((SELECT CASE WHEN lower(p.city)=:city THEN 1 ELSE -1 END FROM resume_profile p
                        WHERE p.version_id=a.version_id AND p.city IS NOT NULL AND {trust} LIMIT 1),0)""")
    if inputs.company:
        params["company"] = norm(inputs.company)
        company = f"""EXISTS (SELECT 1 FROM resume_work w WHERE w.version_id=a.version_id
                   AND lower(w.company)=:company AND {_trusted("w","work.company")}{{record}})"""
        experience.append((company, "w.record_no"))
    if inputs.skill:
        params["skill"] = norm(inputs.skill)
        restrict = ""
        if inputs.section in {"work", "internship", "project"}:
            restrict = """AND EXISTS (
                SELECT 1 FROM resume_record p LEFT JOIN resume_work w USING(version_id,record_no)
                WHERE p.version_id=k.version_id AND p.record_no=k.parent_record_no AND (
                    (:section='project' AND p.kind='project')
                    OR (:section='work' AND p.kind='work' AND w.employment_type IS DISTINCT FROM 'internship')
                    OR (:section='internship' AND p.kind='work' AND w.employment_type='internship')
                ))"""
        skill = f"""EXISTS (SELECT 1 FROM resume_skill k JOIN skill sk ON sk.id=k.skill_id
                 WHERE k.version_id=a.version_id AND (lower(sk.name)=:skill OR EXISTS (
                   SELECT 1 FROM skill_alias sa WHERE sa.skill_id=k.skill_id AND sa.alias_normalized=:skill))
                 AND {_trusted("k","skill.name")} {restrict}{{record}})"""
        experience.append((skill, "k.parent_record_no"))
    if experience and inputs.same_experience and (len(experience) > 1 or groups):
        conditions = " AND ".join(rule.format(record=f" AND {column}=p.record_no") for rule, column in experience)
        criteria.append(f"CASE WHEN EXISTS (SELECT 1 FROM resume_record p WHERE p.version_id=a.version_id AND {conditions}) THEN 1 ELSE 0 END")
    else:
        criteria.extend(f"CASE WHEN {rule.format(record='')} THEN 1 ELSE 0 END" for rule, _ in experience)
    if inputs.school_tag:
        params["tag"] = inputs.school_tag
        criteria.append(f"""CASE WHEN EXISTS (SELECT 1 FROM resume_education e JOIN school_tag t USING(school_id)
                        WHERE e.version_id=a.version_id AND t.scope='school' AND t.tag_code=:tag
                          AND {_trusted("e","education.school_raw")}) THEN 1 ELSE 0 END""")
    if inputs.min_months:
        params["months"] = inputs.min_months
        params["end_month"] = inputs.as_of.year * 12 + inputs.as_of.month
        criteria.append(f"({_months('a')})")
    hard = "LEAST(" + ",".join(["1", *criteria]) + ")"
    match_conditions, scoring = [], []
    for index, (primary, terms) in enumerate(groups):
        params[f"primary{index}"] = _like(primary)
        scoring.append(f"(p.text_norm LIKE :primary{index} ESCAPE '!')::int")
        alternatives = []
        for sub, term in enumerate(terms):
            key = f"term{index}_{sub}"
            params[key] = _like(term)
            alternatives.append(f"p.text_norm LIKE :{key} ESCAPE '!'")
        match_conditions.append("(" + " OR ".join(alternatives) + ")")
    if groups and inputs.same_experience:
        for rule, column in experience:
            match_conditions.append(rule.format(record=f" AND {column}=p.record_no"))
    if groups:
        best = f"""LEFT JOIN LATERAL (
            SELECT p.record_no, {'+'.join(scoring)} AS score
            FROM resume_search_record p JOIN resume_record r USING(version_id,record_no)
            LEFT JOIN resume_work w USING(version_id,record_no)
            WHERE a.hard=1 AND p.version_id=a.version_id AND p.policy_version=:policy
              AND (CAST(:section AS text) IS NULL OR
                CASE WHEN w.employment_type='internship' THEN 'internship' ELSE r.kind END=:section)
              AND {' AND '.join(match_conditions)}
            ORDER BY score DESC,p.record_no LIMIT 1
        ) hit ON true"""
        no_text = "WHEN hit.record_no IS NULL THEN 'no_text'"
    else:
        best = "CROSS JOIN (SELECT NULL::integer AS record_no,0 AS score) hit"
        no_text = ""
    query = f"""
        WITH scoped AS (
            SELECT a.id,a.candidate_id,a.submitted_at,a.active_version_id AS version_id,v.extractor_version
            FROM application a LEFT JOIN resume_version v ON v.id=a.active_version_id
            WHERE {VISIBLE} AND (CAST(:job AS bigint) IS NULL OR a.job_id=:job)
        ), hard AS MATERIALIZED (
            SELECT a.*, CASE WHEN a.extractor_version IS NULL THEN 0 ELSE {hard} END AS hard FROM scoped a
        ), ranked AS (
            SELECT a.*, hit.record_no,coalesce(hit.score,0) AS score,
                   CASE WHEN a.hard=-1 THEN 'reject' WHEN a.hard=0 THEN 'review'
                     {no_text} ELSE 'match' END AS result FROM hard a {best}
        ), dedup AS (
            SELECT *, row_number() OVER (PARTITION BY result,candidate_id ORDER BY score DESC,submitted_at DESC,id DESC) AS n
            FROM ranked
        )
        SELECT id,version_id,record_no,count(*) OVER () AS total FROM dedup WHERE n=1 AND result=:target
        ORDER BY score DESC,submitted_at DESC,id DESC OFFSET :offset LIMIT :limit
    """
    rows = session.execute(text(query), params).all()
    return [list(row[:3]) for row in rows], rows[0][3] if rows else 0


def read_evidence(session, version, policy, record=None):
    return [dict(row) for row in session.execute(text("""
        SELECT DISTINCT e.record_no,e.field_key,e.span_no,s.locator,s.extraction_method,s.ocr_confidence,
            CASE WHEN e.start_offset IS NOT NULL AND e.end_offset <= char_length(s.raw_text)
                 THEN substr(s.raw_text,e.start_offset+1,e.end_offset-e.start_offset) END AS text
        FROM resume_evidence_link e JOIN resume_source_span s USING(version_id,span_no)
        JOIN field_usage_policy p ON p.field_key=e.field_key AND p.purpose='preview' AND p.policy_version=:p AND p.allowed
        JOIN field_definition f ON f.field_key=e.field_key AND f.status='active'
        WHERE e.version_id=:v AND NOT s.is_sensitive AND s.span_kind<>'photo'
          AND (CAST(:r AS integer) IS NULL OR e.record_no=:r)
          AND e.start_offset >= 0 AND e.end_offset > e.start_offset
        ORDER BY e.record_no,e.span_no,e.field_key
    """), {"p": policy, "v": version, "r": record}).mappings() if row["text"] and "█" not in row["text"]]


def _cached_rank(key, compute):
    token = token_hex(16)
    lock = key + ":lock"
    owns_lock = False
    try:
        cache = get_redis()
        existing = cache.get(key)
        if existing is not None:
            return json.loads(existing), True
        owns_lock = bool(cache.set(lock, token, nx=True, ex=8))
        if not owns_lock:
            for _ in range(8):
                sleep(.05)
                existing = cache.get(key)
                if existing is not None:
                    return json.loads(existing), True
            raise HTTPException(503, "查询正在计算，请稍后重试")
    except (RedisError, ValueError):
        if not LIMIT_FALLBACK.acquire(blocking=False):
            raise HTTPException(429, "查询繁忙，请稍后再试")
        try:
            return compute(), False
        finally:
            LIMIT_FALLBACK.release()
    try:
        value = compute()
        try:
            cache.set(key, json.dumps(value), ex=120 + int(token[:2], 16) % 30)
        except RedisError:
            pass
        return value, False
    finally:
        try:
            cache.eval(UNLOCK, 1, lock, token)
        except RedisError:
            pass


def search(session, auth, inputs):
    policy, revision = session.execute(text("SELECT policy_version,search_revision FROM search_state WHERE singleton")).one()
    _check_permissions(session, inputs, policy)
    dictionary, groups = _terms(session, inputs)
    fingerprint = hashlib.sha256(json.dumps([auth.departments, dictionary, inputs.model_dump(mode="json")], sort_keys=True).encode()).hexdigest()
    key = f"rs:search:{auth.user_id}:{auth.perm_version}:{revision}:{policy}:{fingerprint}"
    (ranks, total), hit = _cached_rank(key, lambda: _rank(session, auth, inputs, policy, groups))
    if not ranks:
        return {"items": [], "total": total, "cached": hit, "status": inputs.status}
    rows = {r["id"]: dict(r) for r in session.execute(text(f"""
        SELECT a.id,a.candidate_id,a.active_version_id AS version_id,j.title AS job_title,
               CASE WHEN EXISTS (SELECT 1 FROM field_usage_policy WHERE field_key='profile.name'
                   AND purpose='preview' AND policy_version=:policy AND allowed)
                   THEN (SELECT p.name FROM resume_profile p WHERE p.version_id=a.active_version_id
                    AND {_trusted("p","profile.name")} LIMIT 1) END AS name
        FROM application a JOIN job j ON j.id=a.job_id WHERE {VISIBLE} AND a.id=ANY(CAST(:ids AS bigint[]))
    """), {"ids": [row[0] for row in ranks], "deps": auth.departments, "policy": policy}).mappings()}
    current = session.execute(text("SELECT policy_version,search_revision FROM search_state WHERE singleton")).one()
    if tuple(current) != (policy, revision):
        raise HTTPException(409, "简历或字段策略已变化，请重新查询")
    items = []
    for app_id, version, record in ranks:
        row = rows.get(app_id)
        if row is None or row["version_id"] != version:
            continue
        evidence = read_evidence(session, version, policy, record) if record is not None else []
        needles = {term for _, terms in groups for term in terms}
        evidence.sort(key=lambda entry: -sum(term in norm(entry["text"]) for term in needles))
        items.append({**row, "record_no": record, "evidence": evidence[:5]})
    return {"items": items, "total": total, "cached": hit, "status": inputs.status}
