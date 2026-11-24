"""Bilingual resume search and vector matching against curated capabilities."""
import json
import re
from functools import lru_cache
from time import monotonic

from sqlalchemy import text

from app.core.extraction import norm
from app.core.query_embedding import EmbeddingUnavailable, encode_queries
from app.core.screening import _like
from app.core.vector_index import MODEL_VERSION, SLICE_VERSION

CAPABILITY_THRESHOLD = .50
CAPABILITY_MAX_TAGS = 3
CAPABILITY_SCORE_MARGIN = .10
CAPABILITY_TIME_BUDGET = 6
RESUME_SEARCH_THRESHOLD = .65

HR_ALIASES = (
    "人力资源", "人力", "人事", "hr", "hrbp", "hrd", "hrm", "hr business partner",
    "human resources", "human resource", "people operations", "hr manager", "hr director",
    "人力资源业务伙伴", "人力资源合作伙伴", "人资", "hr generalist",
)
HR_EXPERIENCE = "负责招聘、员工关系、绩效管理和人力资源业务伙伴工作"


def role_pattern(value):
    aliases = HR_ALIASES if value in HR_ALIASES else (value,)
    alternatives = []
    for alias in aliases:
        pattern = re.escape(alias)
        if alias[0].isascii() and alias[0].isalnum():
            pattern = r"(?:^|[^a-z0-9])" + pattern
        if alias[-1].isascii() and alias[-1].isalnum():
            pattern += r"(?:$|[^a-z0-9])"
        alternatives.append(pattern)
    return "|".join(alternatives)


def search_condition(value, allowed, params):
    value = norm(value)
    if not value:
        return None, False
    params.update(semantic_pattern=role_pattern(value), semantic_name=_like(value))
    conditions = ["lower(q.job_title) ~ :semantic_pattern", """EXISTS (
        SELECT 1 FROM resume_search_record sr
        JOIN resume_record r USING(version_id,record_no)
        WHERE sr.version_id=q.version_id AND sr.policy_version=:policy
          AND r.kind IN ('work','project') AND sr.text_norm ~ :semantic_pattern
    )"""]
    if ("profile.name", "search") in allowed:
        conditions.append("q.name ILIKE :semantic_name ESCAPE '!'")
    if ("profile.target_role", "search") in allowed:
        conditions.append("lower(q.target_role) ~ :semantic_pattern")

    role_phrase = HR_EXPERIENCE if value in HR_ALIASES else value
    try:
        vector = encode_queries([role_phrase])[role_phrase]
    except EmbeddingUnavailable:
        return "(" + " OR ".join(conditions) + ")", True
    params.update(semantic_vector=vector, semantic_distance=1 - RESUME_SEARCH_THRESHOLD,
                  semantic_model=MODEL_VERSION, semantic_slice=SLICE_VERSION)
    conditions.extend(["""EXISTS (
        SELECT 1 FROM resume_retrieval_chunk c JOIN resume_chunk_embedding e ON e.chunk_id=c.id
        WHERE c.version_id=q.version_id AND c.policy_version=:policy AND c.slice_version=:semantic_slice
          AND e.model_version=:semantic_model
          AND (e.embedding <=> CAST(:semantic_vector AS vector)) <= :semantic_distance
    )""", """EXISTS (
        SELECT 1 FROM job_requirement r WHERE r.job_id=q.job_id AND r.model_version=:semantic_model
          AND r.kind='role'
          AND (r.embedding <=> CAST(:semantic_vector AS vector)) <= :semantic_distance
    )"""])
    return "(" + " OR ".join(conditions) + ")", False


@lru_cache(maxsize=4)
def _catalog_vectors(phrases):
    return {phrase: json.loads(vector) for phrase, vector in
            encode_queries(phrases, lane="catalog", timeout=CAPABILITY_TIME_BUDGET - 2).items()}


def match_capabilities(session, value):
    groups = session.execute(text("SELECT code,name FROM capability_group ORDER BY display_order,code")).mappings().all()
    skills = set(session.execute(text("""
        SELECT s.capability_code FROM skill s WHERE s.capability_code IS NOT NULL AND (
            lower(s.name)=:value OR EXISTS (SELECT 1 FROM skill_alias a WHERE a.skill_id=s.id AND a.alias_normalized=:value)
        )
    """), {"value": value}).scalars())
    phrases = {
        group["code"]: [norm(group["name"]), *(
            [group["code"].replace("_", " ")] if not group["code"].startswith("custom_") else []
        )] for group in groups
    }
    signature = tuple(sorted({phrase for labels in phrases.values() for phrase in labels}))
    if not signature:
        return {"threshold": CAPABILITY_THRESHOLD, "items": []}
    try:
        deadline = monotonic() + CAPABILITY_TIME_BUDGET
        vectors = _catalog_vectors(signature)
        query = vectors.get(value)
        if query is None:
            query = json.loads(encode_queries([value], timeout=deadline - monotonic())[value])
    except EmbeddingUnavailable:
        found = []
        for group in groups:
            labels = phrases[group["code"]]
            if group["code"] in skills or value in labels:
                found.append((0, group))
            elif len(value) >= 2 and any(value in label or (len(label) >= 2 and label in value) for label in labels):
                found.append((1, group))
        found.sort(key=lambda entry: entry[0])
        return {"threshold": CAPABILITY_THRESHOLD, "mode": "dictionary",
                "items": [dict(group, similarity=None) for _, group in found[:CAPABILITY_MAX_TAGS]]}
    items = []
    for group in groups:
        similarity = 1.0 if group["code"] in skills else min(1.0, max(
            sum(a * b for a, b in zip(query, vectors[label], strict=True)) for label in phrases[group["code"]]))
        if similarity >= CAPABILITY_THRESHOLD:
            items.append(dict(group, similarity=similarity))
    items.sort(key=lambda item: -item["similarity"])
    if items:
        floor = items[0]["similarity"] - CAPABILITY_SCORE_MARGIN
        items = [item for item in items[:CAPABILITY_MAX_TAGS] if item["similarity"] >= floor]
    return {"threshold": CAPABILITY_THRESHOLD, "items": items}
