"""Exact per-requirement evidence retrieval in the authorized application scope."""
from collections import defaultdict
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.extraction import masked
from app.core.job_match_score import DUTY_KINDS, evidence_score, semantic_evidence, skill_pattern, verified_mention
from app.core.job_skills import catalog_for
from app.core.screening import LEVELS, VISIBLE, _months, _trusted
from app.core.vector_index import DEFINITION_VERSION, MODEL_VERSION, SLICE_VERSION

RRF_K = 60
RECALL_DEPTH = 60
GATE_FIELDS = {"degree": "education.degree_level", "months": "work.period"}
JOB_CANDIDATE_SCOPE = """(
    (NOT :unassigned AND a.job_id=:job)
    OR (:unassigned AND a.job_id IS NULL AND a.handling_department_id=(
        SELECT department_id FROM job WHERE id=:job))
)"""
SOURCE_GAP = re.compile(r"[\s，,；;：:、|/·•—–。!?！？（）()\-]*")


def current_job(session, job_id):
    params = {"job": job_id, "model": MODEL_VERSION, "definition": DEFINITION_VERSION}
    source = session.execute(text("""
        SELECT source FROM job_retrieval_index
        WHERE job_id=:job AND model_version=:model AND definition_version=:definition
    """), params).scalar_one_or_none()
    if source is None:
        return None, []
    requirements = session.execute(text("""
        SELECT requirement_no,kind,original_text,mandatory,weight,evidence_kinds,terms,minimum
        FROM job_requirement
        WHERE job_id=:job AND model_version=:model ORDER BY requirement_no
    """), params).mappings().all()
    return source, requirements


def degree_gate(position, alias="a"):
    ranks = "array_position(CAST(:levels AS text[]), e.degree_level)"
    trust = _trusted("e", "education.degree_level")
    return f"""CASE WHEN EXISTS (
        SELECT 1 FROM resume_education e WHERE e.version_id={alias}.version_id AND {trust} AND {ranks}>=:{position}
    ) THEN 1 WHEN EXISTS (
        SELECT 1 FROM resume_education e WHERE e.version_id={alias}.version_id AND {trust} AND {ranks}<:{position}
    ) AND NOT EXISTS (
        SELECT 1 FROM resume_field_issue i WHERE i.version_id={alias}.version_id
            AND i.field_key IN ('education.degree_level','education.school_raw')
    ) AND NOT EXISTS (
        SELECT 1 FROM resume_education e WHERE e.version_id={alias}.version_id AND ({ranks} IS NULL OR NOT ({trust}))
    ) THEN -1 ELSE 0 END"""


def candidate_pool(session, params, requirements, application_id, application_ids=None):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    params.update({"application": application_id, "end_month": today.year * 12 + today.month, "levels": list(LEVELS)})
    gates = []
    for requirement in requirements:
        kind = requirement["kind"]
        if kind not in GATE_FIELDS:
            continue
        number = requirement["requirement_no"]
        key, field = f"minimum_{number}", f"field_{number}"
        params[key] = requirement["minimum"] + int(kind == "degree")
        params[field] = GATE_FIELDS[kind]
        expression = degree_gate(key) if kind == "degree" else f"({_months('a').replace(':months', ':' + key)})"
        gates.append(f"""CASE WHEN EXISTS (
            SELECT 1 FROM field_usage_policy f JOIN field_definition d USING(field_key)
            JOIN search_state s USING(policy_version)
            WHERE f.field_key=:{field} AND f.purpose='filter' AND f.allowed AND d.status='active'
        ) THEN {expression} ELSE 0 END AS gate_{number}""")
    projection = ", " + ", ".join(gates) if gates else ""
    params["workspace_ids"] = application_ids
    distinct = "DISTINCT ON (a.candidate_id)" if application_ids is None else ""
    return session.execute(text(f"""
        WITH chosen AS MATERIALIZED (
            SELECT {distinct} a.id,a.candidate_id,a.active_version_id AS version_id
            FROM application a JOIN resume_version v ON v.id=a.active_version_id AND v.status='published'
            WHERE {VISIBLE} AND {JOB_CANDIDATE_SCOPE} AND a.status<>'closed'
              AND (CAST(:application AS bigint) IS NULL OR a.id=:application)
              AND (CAST(:workspace_ids AS bigint[]) IS NULL OR a.id=ANY(CAST(:workspace_ids AS bigint[])))
            ORDER BY a.candidate_id,v.published_at DESC NULLS LAST,a.active_version_id DESC,a.id DESC
        )
        SELECT a.*,EXISTS (SELECT 1 FROM resume_retrieval_index i
            WHERE i.version_id=a.version_id AND i.model_version=:model AND i.slice_version=:slice) AS indexed
            {projection}
        FROM chosen a ORDER BY a.id
    """), params).mappings().all()


SCOPED_CHUNKS_SQL = """
WITH candidate_ids AS MATERIALIZED (
    SELECT a.id,a.active_version_id AS version_id
    FROM unnest(CAST(:applications AS bigint[]), CAST(:versions AS bigint[])) target(id,version_id)
    JOIN application a ON a.id=target.id AND a.active_version_id=target.version_id
    WHERE """ + VISIBLE + " AND " + JOB_CANDIDATE_SCOPE + """ AND a.status<>'closed'
), chunks AS MATERIALIZED (
    SELECT a.id AS application_id,c.id AS chunk_id,c.version_id,c.record_no,c.evidence_kind,c.body,e.embedding
    FROM candidate_ids a JOIN resume_retrieval_chunk c USING(version_id)
    JOIN search_state s USING(policy_version)
    JOIN resume_chunk_embedding e ON e.chunk_id=c.id AND e.model_version=:model
    WHERE c.slice_version=:slice
)
"""

VECTOR_SCORE_SQL = SCOPED_CHUNKS_SQL + """
SELECT c.application_id,r.requirement_no,max(1-(c.embedding <=> r.embedding)) AS relevance
FROM chunks c JOIN job_requirement r ON c.evidence_kind=ANY(r.evidence_kinds)
WHERE r.job_id=:job AND r.model_version=:model AND r.kind IN ('duty','role')
GROUP BY c.application_id,r.requirement_no
"""

RECALL_SQL = SCOPED_CHUNKS_SQL + """
, requirements AS MATERIALIZED (
    SELECT * FROM job_requirement WHERE job_id=:job AND model_version=:model AND kind IN ('duty','role','qualification')
), vector_windows AS (
    SELECT 'vector' AS lane,c.application_id,c.chunk_id,c.record_no,r.requirement_no,
           1-(c.embedding <=> r.embedding) AS relevance
    FROM chunks c JOIN requirements r ON c.evidence_kind=ANY(r.evidence_kinds)
), keyword_terms AS (
    SELECT r.requirement_no,r.evidence_kinds,keyword.term FROM requirements r
    CROSS JOIN LATERAL (
        SELECT term FROM unnest(r.terms) terms(term)
        UNION
        SELECT t.term_norm FROM search_term_group g JOIN search_term t ON t.group_id=g.id
        WHERE g.enabled AND g.dict_version=(SELECT max(dict_version) FROM search_term_group WHERE enabled)
          AND g.section_type IS NULL
          AND EXISTS (SELECT 1 FROM search_term original WHERE original.group_id=g.id AND original.term_norm=ANY(r.terms))
    ) keyword
), keyword_windows AS (
    SELECT 'keyword' AS lane,c.application_id,c.chunk_id,c.record_no,k.requirement_no,
        max(CASE WHEN strpos(lower(c.body),k.term)>0 THEN 1.0
             WHEN char_length(k.term)>=3 THEN strict_word_similarity(k.term,c.body) ELSE 0 END) AS relevance
    FROM chunks c JOIN keyword_terms k ON c.evidence_kind=ANY(k.evidence_kinds)
    GROUP BY c.application_id,c.chunk_id,c.record_no,k.requirement_no
), unique_experiences AS (
    SELECT DISTINCT ON (lane,application_id,requirement_no,record_no)
           lane,application_id,requirement_no,record_no,chunk_id,relevance
    FROM (
        SELECT * FROM vector_windows
        UNION ALL SELECT * FROM keyword_windows WHERE relevance>=0.3
    ) all_windows
    ORDER BY lane,application_id,requirement_no,record_no,relevance DESC,chunk_id
)
SELECT DISTINCT ON (lane,application_id,requirement_no)
       lane,application_id,requirement_no,record_no,chunk_id,relevance,evidence_kind
FROM unique_experiences u JOIN chunks c USING(chunk_id,application_id,record_no)
ORDER BY lane,application_id,requirement_no,relevance DESC,chunk_id
"""

SKILL_SQL = """
WITH targets AS (
    SELECT * FROM unnest(CAST(:applications AS bigint[]),CAST(:versions AS bigint[])) candidate(id,version_id)
), matches AS (
    SELECT target.id AS application_id,k.skill_id,k.record_no,k.parent_record_no,s.span_no,
           s.raw_text,
           p.start_offset,p.end_offset,
           CASE WHEN r.kind='work' AND w.employment_type='internship' THEN 'internship'
                WHEN r.kind='work' THEN 'work'
                WHEN r.kind='project' THEN coalesce((
                    SELECT c.evidence_kind FROM resume_retrieval_chunk c JOIN search_state st USING(policy_version)
                    WHERE c.version_id=target.version_id AND c.record_no=r.record_no AND c.slice_version=:slice
                    ORDER BY c.chunk_no LIMIT 1),'project') END AS evidence_kind
    FROM targets target JOIN application a ON a.id=target.id AND a.active_version_id=target.version_id
    JOIN resume_skill k ON k.version_id=target.version_id AND k.skill_id=ANY(CAST(:skills AS bigint[]))
    JOIN resume_record_source p ON p.version_id=k.version_id AND p.record_no=k.record_no AND p.field_key='skill.name'
    JOIN resume_source_span s ON s.version_id=p.version_id AND s.span_no=p.span_no
    LEFT JOIN resume_record r ON r.version_id=k.version_id AND r.record_no=k.parent_record_no
    LEFT JOIN resume_work w ON w.version_id=r.version_id AND w.record_no=r.record_no
    WHERE """ + VISIBLE + " AND " + JOB_CANDIDATE_SCOPE + """ AND a.status<>'closed'
      AND NOT s.is_sensitive AND s.span_kind<>'photo' AND (s.ocr_confidence IS NULL OR s.ocr_confidence>=.9)
      AND p.end_offset<=char_length(s.raw_text)
      AND NOT EXISTS (SELECT 1 FROM resume_field_issue i
          WHERE i.version_id=k.version_id AND i.record_no=k.record_no AND i.field_key='skill.name')
      AND EXISTS (SELECT 1 FROM field_usage_policy f JOIN field_definition d USING(field_key)
          JOIN search_state st USING(policy_version)
          WHERE f.field_key='skill.name' AND f.purpose='filter' AND f.allowed AND d.status='active')
)
SELECT * FROM matches ORDER BY application_id,skill_id,parent_record_no,evidence_kind,span_no,start_offset
"""


EXPERIENCE_SKILL_SQL = """
    SELECT a.id AS application_id,c.record_no,c.evidence_kind,c.body
    FROM unnest(CAST(:applications AS bigint[]),CAST(:versions AS bigint[])) target(id,version_id)
    JOIN application a ON a.id=target.id AND a.active_version_id=target.version_id
    JOIN resume_retrieval_chunk c ON c.version_id=target.version_id AND c.slice_version=:slice
    JOIN search_state st USING(policy_version)
    WHERE """ + VISIBLE + " AND " + JOB_CANDIDATE_SCOPE + """ AND a.status<>'closed'
      AND EXISTS (SELECT 1 FROM field_usage_policy f JOIN field_definition d USING(field_key)
          WHERE f.field_key='skill.name' AND f.purpose='filter' AND f.allowed
            AND f.policy_version=st.policy_version AND d.status='active')
    ORDER BY a.id,c.record_no,c.chunk_no
"""


def skill_hits(session, params, requirements):
    skills = [item for item in requirements if item["kind"] == "skill"]
    if not skills:
        return {}
    catalog = catalog_for(session)
    by_requirement = {
        item["requirement_no"]: {sid: (name, aliases) for name, (sid, aliases) in catalog.items()
                                 if aliases.intersection(item["terms"])}
        for item in skills
    }
    patterns = {
        item["requirement_no"]: skill_pattern(set(item["terms"]).union(
            *(aliases for _, aliases in by_requirement[item["requirement_no"]].values())))
        for item in skills
    }
    skill_ids = sorted({sid for matches in by_requirement.values() for sid in matches})
    structured = session.execute(text(SKILL_SQL), {**params, "skills": skill_ids}).mappings() if skill_ids else []
    hits = {}
    for row in structured:
        for item in skills:
            req_no = item["requirement_no"]
            key = row["application_id"], req_no
            entry = by_requirement[req_no].get(row["skill_id"])
            if not entry or (item["evidence_kinds"] and row["evidence_kind"] not in item["evidence_kinds"]):
                continue
            context = row["raw_text"][max(0, row["start_offset"] - 12):row["end_offset"] + 8]
            if verified_mention(patterns[req_no], context):
                current = hits.get(key)
                if not current or (not current["evidence_kind"] and row["evidence_kind"]):
                    hits[key] = {"record_no": row["parent_record_no"] or row["record_no"],
                                 "evidence_kind": row["evidence_kind"], "skill": entry[0], "method": "structured"}
    for row in session.execute(text(EXPERIENCE_SKILL_SQL), params).mappings():
        for item in skills:
            req_no = item["requirement_no"]
            key = row["application_id"], req_no
            if key in hits or (item["evidence_kinds"] and row["evidence_kind"] not in item["evidence_kinds"]):
                continue
            if match := verified_mention(patterns[req_no], row["body"]):
                hits[key] = {"record_no": row["record_no"], "evidence_kind": row["evidence_kind"],
                             "skill": match, "method": "keyword"}
    return hits


def rank_lane(edges, weights):
    """Fuse requirement ranks within a lane, with ties sharing the same rank."""
    scores = defaultdict(float)
    by_requirement = defaultdict(list)
    for (app_id, req_no), edge in edges.items():
        by_requirement[req_no].append((edge["relevance"], app_id))
    for req_no, rows in by_requirement.items():
        last, rank = None, 0
        for index, (score, app_id) in enumerate(sorted(rows, reverse=True), 1):
            if score != last:
                last, rank = score, index
            if rank > RECALL_DEPTH:
                break
            scores[app_id] += weights[req_no] / (RRF_K + rank)
    return scores


def fuse(lanes):
    scores = defaultdict(float)
    for lane in lanes:
        last, rank = None, 0
        for index, (app_id, score) in enumerate(sorted(lane.items(), key=lambda pair: (-pair[1], pair[0])), 1):
            if score != last:
                last, rank = score, index
            if rank > RECALL_DEPTH:
                break
            scores[app_id] += 1 / (RRF_K + rank)
    return sorted(scores, key=lambda app_id: (-scores[app_id], -app_id))


def full_record_evidence(session, version, records):
    if not records:
        return []
    rows = session.execute(text("""
        SELECT DISTINCT p.record_no,s.span_no,s.reading_order,s.locator,s.raw_text,p.start_offset,p.end_offset
        FROM resume_record_source p JOIN resume_source_span s USING(version_id,span_no)
        JOIN field_usage_policy f ON f.field_key=p.field_key AND f.purpose='preview' AND f.allowed
        JOIN field_definition d ON d.field_key=f.field_key AND d.status='active'
        JOIN search_state st USING(policy_version)
        WHERE p.version_id=:version AND p.record_no=ANY(CAST(:records AS integer[]))
          AND p.field_key IN ('work.company','work.title','work.period','work.employment_type','work.description',
                              'project.project_name','project.role','project.period','project.description','skill.name')
          AND NOT s.is_sensitive AND s.span_kind<>'photo'
          AND p.end_offset<=char_length(s.raw_text)
        ORDER BY p.record_no,s.reading_order,s.span_no,p.start_offset,p.end_offset
    """), {"version": version, "records": list(records)}).mappings()
    per_record = defaultdict(dict)
    for row in rows:
        span = per_record[row["record_no"]].setdefault(row["span_no"], {
            "raw": row["raw_text"], "locator": row["locator"], "ranges": [],
        })
        start, end = row["start_offset"], row["end_offset"]
        ranges = span["ranges"]
        if ranges and (start <= ranges[-1][1] or SOURCE_GAP.fullmatch(span["raw"][ranges[-1][1]:start])):
            ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
        else:
            ranges.append((start, end))

    blocks = []
    for record, spans in per_record.items():
        paragraphs, locations = [], []
        for span_no, span in spans.items():
            excerpt = " ".join(masked(span["raw"][start:end]) for start, end in span["ranges"]).strip()
            if excerpt:
                paragraphs.append(excerpt)
                locations.append({"span_no": span_no, "locator": span["locator"]})
        if paragraphs:
            blocks.append({"record_no": record, "kind": records[record], "text": "\n".join(paragraphs),
                           "locations": locations})
    return blocks


def retrieval(session, departments, job_id, application_id=None, *, unassigned=False, workspace_ids=None):
    source, requirements = current_job(session, job_id)
    if source is None:
        return {"source": "pending", "order": [], "candidates": {}, "indexed": 0, "eligible": 0}

    session.execute(text("SET LOCAL jit=off"))
    params = {"deps": departments, "job": job_id, "model": MODEL_VERSION, "slice": SLICE_VERSION, "unassigned": unassigned}
    pool = candidate_pool(session, params, requirements, application_id, workspace_ids)
    gates = [item["requirement_no"] for item in requirements if item["kind"] in GATE_FIELDS]
    gate_values = {row["id"]: {number: row[f"gate_{number}"] for number in gates} for row in pool}
    eligible = [row for row in pool if all(value == 1 for value in gate_values[row["id"]].values())]
    eligible_ids = {row["id"] for row in eligible}
    params.update({"applications": [row["id"] for row in eligible], "versions": [row["version_id"] for row in eligible]})
    lanes = {"vector": {}, "keyword": {}}
    if eligible:
        query = VECTOR_SCORE_SQL if workspace_ids is not None else RECALL_SQL
        for row in session.execute(text(query), params).mappings():
            lanes[row.get("lane", "vector")][row["application_id"], row["requirement_no"]] = row
    skills = skill_hits(session, params, requirements) if eligible else {}
    weights = {item["requirement_no"]: item["weight"] for item in requirements}
    rank_scores = {lane: rank_lane(edges, weights) for lane, edges in lanes.items()}
    rank_scores["structured"] = rank_lane({key: {"relevance": 1} for key in skills}, weights)
    recall_order = {app_id: index for index, app_id in enumerate(fuse(rank_scores.values()))}
    duty_items = [item["requirement_no"] for item in requirements if item["kind"] in DUTY_KINDS]
    skill_items = [item["requirement_no"] for item in requirements if item["kind"] == "skill"]
    candidates = {}
    for row in pool:
        app_id = row["id"]
        states = gate_values[app_id].values()
        if -1 in states:
            status = "outside_requirements"
        elif 0 in states:
            status = "needs_review"
        elif not row["indexed"]:
            status = "unindexed"
        else:
            status = "ranked" if app_id in recall_order else "no_evidence"
        score = None
        if app_id in eligible_ids and row["indexed"] and (duty_items or skill_items):
            duties = {number: semantic_evidence(lanes["vector"].get((app_id, number), {}).get("relevance"))
                      for number in duty_items}
            skill_scores = {number: 1.0 for number in skill_items if (app_id, number) in skills}
            score = evidence_score(requirements, duties, skill_scores)
        candidates[app_id] = {"version_id": row["version_id"], "status": status, "score": score,
                              "skill_coverage": score["skill"] if score and skill_items else None}
        if workspace_ids is not None and score:
            candidates[app_id]["match_skills"] = [
                {"name": item["original_text"], "matched": (app_id, item["requirement_no"]) in skills}
                for item in requirements if item["kind"] == "skill"
            ]

    def order_key(app_id):
        candidate = candidates[app_id]
        score = candidate["score"]
        return (score is None, -score["total"] if score else 0,
                recall_order.get(app_id, len(pool)), -app_id)

    order = sorted(candidates, key=order_key)
    result = {"source": source, "candidates": candidates, "order": order,
              "indexed": sum(row["indexed"] for row in pool), "eligible": len(eligible),
              "requirements_count": len(requirements)}
    if application_id is None or application_id not in candidates:
        return result

    app_id = application_id
    details = []
    records = {}
    for item in requirements:
        number, kind = item["requirement_no"], item["kind"]
        key = app_id, number
        method, evidence_kind, matched_skill, record_no, points = None, None, None, None, None
        if kind in GATE_FIELDS:
            status = {-1: "not_met", 0: "unverified", 1: "supported"}[gate_values[app_id][number]]
        elif kind == "qualification_check":
            status = "unverified"
        elif candidates[app_id]["status"] in ("outside_requirements", "needs_review", "unindexed"):
            status = "not_checked"
        elif kind == "skill":
            hit = skills.get(key)
            status = "supported" if hit else "unverified"
            points = 100 if hit else 0
            if hit:
                method, evidence_kind, matched_skill = hit["method"], hit["evidence_kind"], hit["skill"]
                record_no = hit["record_no"]
        else:
            keyword, vector = lanes["keyword"].get(key), lanes["vector"].get(key)
            clue = vector if kind in DUTY_KINDS else keyword if keyword and keyword["relevance"] == 1 else vector or keyword
            points = round(100 * semantic_evidence(vector["relevance"] if vector else None), 2) if kind in DUTY_KINDS else None
            status = "related" if clue else "unverified"
            if clue:
                method = "keyword" if clue is keyword else "vector"
                evidence_kind, record_no = clue["evidence_kind"], clue["record_no"]
        if record_no is not None:
            records[record_no] = evidence_kind or "skill"
        details.append({
            "id": number, "kind": kind, "text": item["original_text"], "status": status,
            "mandatory": item["mandatory"], "weight": item["weight"], "scopes": item["evidence_kinds"],
            "method": method, "evidence_kind": evidence_kind, "matched_skill": matched_skill,
            "record_no": record_no, "points": points,
        })
    blocks = full_record_evidence(session, candidates[app_id]["version_id"], records)
    return {**result, "details": details, "evidence_blocks": blocks}
