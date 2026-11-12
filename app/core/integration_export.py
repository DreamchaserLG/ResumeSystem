"""Read-only export of a scoped application snapshot and its approved evidence."""
from collections import defaultdict
from dataclasses import dataclass, replace
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.integration import IntegrationScope, PRIVATE_FIELD

# All SQL identifiers come from this module, never from a request or a client whitelist.
FILTERS = {
    "degree_level": ("education.degree_level", "resume_education x", "x.degree_level"),
    "school": ("education.school_raw", "resume_education x", "x.school_raw"),
    "company": ("work.company", "resume_work x", "x.company"),
    "city": ("profile.city", "resume_profile x", "x.city"),
    "skill": ("skill.name", "resume_skill x JOIN skill sk ON sk.id = x.skill_id", "sk.name"),
}
SCOPE = """
    a.job_id = ANY(CAST(:jobs AS bigint[])) AND EXISTS (
        SELECT 1 FROM integration_job ij
        WHERE ij.client_id = :client_id AND ij.job_id = a.job_id
          AND (ij.handling_department_id IS NULL OR ij.handling_department_id = a.handling_department_id)
    )
"""
HEAD = """
    SELECT a.id AS application_id, a.job_id, j.title AS job_title, a.status,
           v.id AS version_id, v.version_no, v.parser_version, v.published_at
    FROM application a JOIN job j ON j.id = a.job_id
"""
CURRENT = "LEFT JOIN resume_version v ON v.id = a.active_version_id AND v.status IN ('published', 'superseded')"
HISTORY = """
    JOIN application_version_history h ON h.application_id = a.id AND h.version_id = :version_id
    JOIN resume_version v ON v.id = h.version_id AND v.status IN ('published', 'superseded')
"""
PRIVATE_VALUE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{17}[\dXx](?!\w)|(?:婚育|婚姻|身份证|性别|生育情况|孕育情况|电话|手机|邮箱|"
    r"marital status|passport no\.?|social security)\s*[:：]|(?:已婚|未婚)(?![\u3400-\u9fff])",
    re.IGNORECASE,
)
PERIOD = re.compile(r"^(\d{4})(?:[./年-](\d{1,2})(?:月|[./-](\d{1,2})日?)?)?年?$")


def scope_params(scope: IntegrationScope) -> dict:
    return {"jobs": scope.job_ids, "client_id": scope.client_id}


def find_applications(session: Session, scope: IntegrationScope, filters: dict, after: int, limit: int) -> list:
    predicates = [SCOPE, "a.id > :after"]
    params = {**scope_params(scope), "after": after, "limit": limit}
    if filters.get("job_id") is not None:
        predicates.append("a.job_id = :job_id")
        params["job_id"] = filters["job_id"]
    for name, (field_key, table, column) in FILTERS.items():
        if name not in filters or filters[name] is None:
            continue
        predicates.append(f"""EXISTS (
            SELECT 1 FROM {table} WHERE x.version_id = v.id AND lower({column}) = lower(:{name})
            AND EXISTS (
                SELECT 1 FROM resume_evidence_link e JOIN resume_source_span s
                  ON s.version_id = e.version_id AND s.span_no = e.span_no
                WHERE e.version_id = x.version_id AND e.record_no = x.record_no AND e.field_key = :{name}_field
                  AND NOT s.is_sensitive AND s.span_kind <> 'photo'
                  AND e.start_offset >= 0 AND e.end_offset > e.start_offset AND e.end_offset <= char_length(s.raw_text)
            ) AND NOT EXISTS (
                SELECT 1 FROM resume_field_issue i WHERE i.version_id = x.version_id
                  AND i.record_no = x.record_no AND i.field_key = :{name}_field
            )
        )""")
        params[name] = filters[name]
        params[f"{name}_field"] = field_key
    query = f"{HEAD} {CURRENT} WHERE {' AND '.join(predicates)} ORDER BY a.id LIMIT :limit"
    return session.execute(text(query), params).mappings().all()


def find_application(session: Session, scope: IntegrationScope, application_id: int, version_id: int | None):
    join = CURRENT if version_id is None else HISTORY
    return session.execute(
        text(f"{HEAD} {join} WHERE a.id = :application_id AND {SCOPE}"),
        {**scope_params(scope), "application_id": application_id, "version_id": version_id},
    ).mappings().one_or_none()


def application_summary(row) -> dict:
    version = None if row["version_id"] is None else {
        key: row[key] for key in ("version_id", "version_no", "parser_version", "published_at")
    }
    return {
        "application_id": row["application_id"],
        "job": {"id": row["job_id"], "title": row["job_title"]},
        "status": row["status"], "resume_version": version,
    }


@dataclass(frozen=True)
class Source:
    record_no: int
    field_key: str
    span_no: int
    start: int
    end: int
    locator: dict
    excerpt: str
    method: str
    confidence: float | None

    def reference(self, version_id: int) -> dict:
        return {"version_id": version_id, "span_no": self.span_no,
                "start_offset": self.start, "end_offset": self.end}


def _safe_locator(locator: dict) -> dict:
    return {
        key: value for key, value in locator.items()
        if key in {"page", "page_no", "docx_para", "docx_table", "bbox"}
        and (type(value) is int or isinstance(value, list) and all(type(part) in (int, float) for part in value))
    }


def evidence_for(session: Session, version_id: int, fields: frozenset[str],
                 span_no: int | None = None) -> dict[tuple[int, str], list[Source]]:
    records = defaultdict(list)
    if not fields:
        return records
    for row in session.execute(text("""
        SELECT e.record_no, e.field_key, e.span_no, e.start_offset, e.end_offset,
               s.raw_text, s.locator, s.extraction_method, s.ocr_confidence
        FROM resume_evidence_link e JOIN resume_source_span s USING (version_id, span_no)
        WHERE e.version_id = :version_id AND e.field_key = ANY(CAST(:fields AS text[]))
          AND NOT s.is_sensitive AND s.span_kind <> 'photo'
          AND (CAST(:span_no AS bigint) IS NULL OR e.span_no = :span_no)
        ORDER BY e.record_no, e.field_key, e.span_no
    """), {"version_id": version_id, "fields": sorted(fields), "span_no": span_no}).mappings():
        start, end, raw = row["start_offset"], row["end_offset"], row["raw_text"]
        if raw is None or start is None or end is None or not 0 <= start < end <= len(raw):
            continue
        excerpt = raw[start:end]
        if not excerpt.strip() or PRIVATE_VALUE.search(excerpt):
            continue
        # Reject a range intersecting a private token even when its slice starts inside that token.
        if any(match.start() < end and match.end() > start for match in PRIVATE_VALUE.finditer(raw)):
            continue
        source = Source(row["record_no"], row["field_key"], row["span_no"], start, end,
                        _safe_locator(row["locator"]), excerpt, row["extraction_method"], row["ocr_confidence"])
        records[(source.record_no, source.field_key)].append(source)
    return records


def _date_value(value, raw: str | None) -> str | None:
    match = PERIOD.fullmatch((raw or "").strip())
    if match:
        year, month, day = match.groups()
        if month is None:
            return year
        if 1 <= int(month) <= 12 and day is None:
            return f"{year}-{int(month):02d}"
    return value.isoformat() if value is not None else None


@dataclass
class ExportedResume:
    version_id: int
    fields: frozenset[str]
    sources: dict[tuple[int, str], list[Source]]
    issues: dict[tuple[int, str], str]

    def field_meta(self, record_no: int, key: str) -> dict | None:
        if key not in self.fields:
            return None
        sources = self.sources.get((record_no, key), [])
        issue = self.issues.get((record_no, key))
        if not sources and issue is None:
            return None
        confidence = [s.confidence for s in sources if s.confidence is not None]
        return {"confidence": min(confidence, default=None),
                "extraction": sources[0].method if sources else None,
                "evidence": [s.reference(self.version_id) for s in sources],
                "issues": [issue] if issue else []}

    def record(self, record_no: int, values: dict[str, dict[str, Any]]) -> dict:
        output, metadata = {}, {}
        for key, items in values.items():
            meta = self.field_meta(record_no, key)
            if meta is None:
                continue
            approved = {name: value for name, value in items.items() if value is not None
                        and (not isinstance(value, str) or not PRIVATE_VALUE.search(value))}
            if meta["evidence"] and approved:
                output.update(approved)
                metadata[key] = meta
            elif meta["issues"]:
                metadata[key] = {**meta, "evidence": []}
        if metadata:
            confidences = [meta["confidence"] for meta in metadata.values() if meta["confidence"] is not None]
            refs = {tuple(ref.values()): ref for meta in metadata.values() for ref in meta["evidence"]}
            output["_meta"] = {
                "record_no": record_no, "confidence": min(confidences, default=None),
                "extraction": next((m["extraction"] for m in metadata.values() if m["extraction"]), None),
                "evidence": list(refs.values()),
                "issues": sorted({issue for meta in metadata.values() for issue in meta["issues"]}),
                "fields": metadata,
            }
        return output


def _rows(session: Session, query: str, version_id: int):
    return session.execute(text(query), {"version_id": version_id}).mappings()


def export_detail(session: Session, scope: IntegrationScope, row,
                  span_no: int | None = None) -> tuple[dict, list[Source]]:
    body = {**application_summary(row), "basics": {}, "work": [], "education": [], "skills": [],
            "projects": [], "extensions": [], "schema_version": "1.0.0"}
    version_id = row["version_id"]
    if version_id is None or not scope.export_fields:
        return body, []
    sources = evidence_for(session, version_id, scope.export_fields, span_no)
    issues = {(r["record_no"], r["field_key"]): r["reason"] for r in _rows(
        session, "SELECT record_no, field_key, reason FROM resume_field_issue WHERE version_id = :version_id", version_id
    ) if r["field_key"] in scope.export_fields}
    export = ExportedResume(version_id, scope.export_fields, sources, issues)
    exported_keys: set[tuple[int, str]] = set()

    def add(section: str, record_no: int, values: dict) -> None:
        result = export.record(record_no, values)
        if not result:
            return
        for key, shape in values.items():
            if any(name in result for name in shape) and sources.get((record_no, key)):
                exported_keys.add((record_no, key))
        if section == "basics":
            if "city" in result:
                result["location"] = {"city": result.pop("city")}
            body[section] = result
        else:
            body[section].append(result)

    for p in _rows(session, "SELECT record_no, name, city FROM resume_profile WHERE version_id = :version_id ORDER BY record_no LIMIT 1", version_id):
        add("basics", p["record_no"], {"profile.name": {"name": p["name"]}, "profile.city": {"city": p["city"]}})
    for w in _rows(session, "SELECT * FROM resume_work WHERE version_id = :version_id ORDER BY record_no", version_id):
        description = "\n".join(s.excerpt for s in sources.get((w["record_no"], "work.description"), []))
        add("work", w["record_no"], {
            "work.company": {"company": w["company"]}, "work.title": {"position": w["title"]},
            "work.description": {"summary": description or None}, "work.employment_type": {"employmentType": w["employment_type"]},
            "work.period": {"startDate": _date_value(w["start_date"], w["start_period_raw"]),
                            "endDate": _date_value(w["end_date"], w["end_period_raw"]),
                            "isCurrent": True if w["is_current"] else None},
        })
    tags = defaultdict(list)
    if "education.school_tags" in scope.export_fields:
        for tag in _rows(session, """
            SELECT e.record_no, t.tag_code, t.scope, t.subject, t.list_version, t.source
            FROM resume_education e JOIN school_tag t USING (school_id)
            WHERE e.version_id = :version_id ORDER BY e.record_no, t.tag_code, t.scope, t.subject, t.list_version
        """, version_id):
            tags[tag["record_no"]].append({key: value for key, value in tag.items() if key != "record_no"})
        for record_no in tags:
            # A dictionary tag is derived from the school that the source actually identifies.
            sources[(record_no, "education.school_tags")] = [
                replace(source, field_key="education.school_tags")
                for source in sources.get((record_no, "education.school_raw"), [])
            ]
    for e in _rows(session, "SELECT * FROM resume_education WHERE version_id = :version_id ORDER BY record_no", version_id):
        add("education", e["record_no"], {
            "education.school_raw": {"institution": e["school_raw"]},
            "education.school_tags": {"schoolTags": tags.get(e["record_no"])},
            "education.degree_level": {"studyType": e["degree_level"]}, "education.major": {"area": e["major"]},
            "education.period": {"startDate": _date_value(e["start_date"], e["start_period_raw"]),
                                 "endDate": _date_value(e["end_date"], e["end_period_raw"])},
        })
    for s in _rows(session, """SELECT r.record_no, s.name FROM resume_skill r JOIN skill s ON s.id = r.skill_id
                              WHERE r.version_id = :version_id ORDER BY r.record_no""", version_id):
        add("skills", s["record_no"], {"skill.name": {"name": s["name"]}})
    for p in _rows(session, "SELECT * FROM resume_project WHERE version_id = :version_id ORDER BY record_no", version_id):
        add("projects", p["record_no"], {"project.project_name": {"name": p["project_name"]},
                                        "project.role": {"role": p["role"]}})
    for extra in _rows(session, """
        SELECT x.record_no, x.field_key, x.span_no, x.payload, f.display_name, f.value_type
        FROM resume_extra_value x JOIN field_definition f USING (field_key)
        WHERE x.version_id = :version_id AND f.status = 'active' ORDER BY x.record_no
    """, version_id):
        key, record_no = extra["field_key"], extra["record_no"]
        refs = [s for s in sources.get((record_no, key), []) if s.span_no == extra["span_no"]]
        if not refs or PRIVATE_FIELD.search(extra["display_name"]):
            continue
        payload = extra["payload"]
        if not isinstance(payload, dict):
            continue
        value = payload.get("value", payload.get("raw"))
        if type(value) not in (str, int, float, bool) or isinstance(value, str) and PRIVATE_VALUE.search(value):
            continue
        body["extensions"].append({"field_key": key, "label": extra["display_name"], "value": value,
                                   "type": extra["value_type"], "confidence": refs[0].confidence,
                                   "source_span": refs[0].reference(version_id)})
        sources[(record_no, key)] = refs
        exported_keys.add((record_no, key))
    return body, [s for key in sorted(exported_keys) for s in sources[key]]
