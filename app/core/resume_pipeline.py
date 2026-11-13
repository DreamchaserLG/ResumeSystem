"""Persist extracted records, source ranges and the approved keyword projection."""
from collections import defaultdict
import hashlib
import json

from sqlalchemy import text

from app.core.extraction import RULE_VERSION, extract_document, norm

DETAILS = {
    "profile": ("resume_profile", ("name", "city", "photo_present", "target_role")),
    "contact": ("resume_contact", ("type", "value")),
    "education": ("resume_education", ("school_raw", "school_id", "degree_level", "major", "start_date", "end_date", "start_period_raw", "end_period_raw")),
    "work": ("resume_work", ("company", "title", "employment_type", "start_date", "end_date", "start_period_raw", "end_period_raw", "is_current", "description")),
    "project": ("resume_project", ("project_name", "role", "start_date", "end_date", "start_period_raw", "end_period_raw", "is_current", "description")),
    "skill": ("resume_skill", ("skill_id", "parent_record_no", "language_priority")),
    "extension": ("resume_extra_value", ("field_key", "raw_label", "payload", "span_no", "rule_version", "reviewed")),
}
COPY_TABLES = {
    "resume_source_span": ("span_no", "span_kind", "raw_text", "locator", "extraction_method", "is_sensitive", "ocr_confidence", "reading_order"),
    "resume_record": ("record_no", "kind", "review_anchor"),
    **{table: ("record_no", *columns) for table, columns in DETAILS.values()},
    "resume_evidence_link": ("record_no", "field_key", "span_no", "start_offset", "end_offset"),
    "resume_field_issue": ("record_no", "field_key", "span_no", "reason"),
    "resume_record_source": ("record_no", "part_no", "span_no", "field_key", "start_offset", "end_offset"),
}


def prepare_extraction(connection, document):
    aliases = {norm(a): b for a, b in connection.execute(text("SELECT alias_normalized,field_key FROM field_alias"))}
    skills = {norm(a): b for a, b in connection.execute(text(
        "SELECT name,id FROM skill UNION ALL SELECT alias_normalized,skill_id FROM skill_alias"
    ))}
    schools = defaultdict(set)
    for alias, key in connection.execute(text(
        "SELECT canonical_name,id FROM school UNION ALL SELECT alias_normalized,school_id FROM school_alias"
    )):
        schools[norm(alias)].add(key)
    languages = set(connection.execute(text("SELECT id FROM skill WHERE is_programming_language")).scalars())
    return extract_document(document, aliases, skills, schools, languages)


def insert(connection, table, rows):
    if not rows:
        return
    keys = tuple(rows[0])
    binds = [f"CAST(:{key} AS jsonb)" if key in {"payload", "locator", "detail"} else f":{key}" for key in keys]
    connection.execute(text(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(binds)})"), rows)


def _detail(record, record_numbers):
    values = {key.split(".")[-1]: value for key, value in record.fields.items() if "." in key}
    kind = record.kind
    if kind == "profile":
        values.setdefault("photo_present", False)
    elif kind == "contact":
        key, value = next(iter(record.fields.items()), ("contact.other", None))
        return {"type": key.split(".")[-1], "value": value} if value else None
    elif kind == "skill":
        skill = record.fields.get("skill.name")
        return {"skill_id": skill, "parent_record_no": record_numbers.get(id(record.parent)),
                "language_priority": record.fields.get("language_priority")} if skill else None
    elif kind == "extension":
        key, label, payload = record.fields["extension"]
        return {"field_key": key, "raw_label": label, "payload": json.dumps(payload, ensure_ascii=False),
                "span_no": record.sources["extension"][0].span, "rule_version": RULE_VERSION, "reviewed": False}
    elif kind in {"work", "project", "education"}:
        required = {"work": "company", "project": "project_name", "education": "school_raw"}[kind]
        if not values.get(required):
            return None
        values.update(values.get("period", {}))
        values["description"] = "\n".join(src.value for src in record.sources.get(f"{kind}.description", [])) or None
        values.setdefault("is_current", False)
        values["school_id"] = record.fields.get("school_id")
    return values


def record_anchor(kind, evidence):
    if kind == "profile":
        return "profile"
    raw = json.dumps([kind, sorted(set(evidence))], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def write_extraction(connection, version_id, records):
    numbers = {id(record): number for number, record in enumerate(records, 1)}
    insert(connection, "resume_record", [
        {"version_id": version_id, "record_no": numbers[id(record)], "kind": record.kind,
         "review_anchor": record_anchor(record.kind, [
             (src.span, src.start, src.end) for sources in record.sources.values() for src in sources if src.end > src.start
         ])}
        for record in records
    ])
    fields = set(connection.execute(text("SELECT field_key FROM field_definition WHERE status='active'")).scalars())
    batches = defaultdict(list)
    for record in records:
        context = {"version_id": version_id, "record_no": numbers[id(record)]}
        if record.kind in DETAILS:
            table, columns = DETAILS[record.kind]
            detail = _detail(record, numbers)
            if detail is not None:
                batches[table].append({**context, **{column: detail.get(column) for column in columns}})
        part = 0
        for key, sources in record.sources.items():
            if key == "extension":
                key = record.fields["extension"][0]
            grouped = defaultdict(list)
            for source in sources:
                if source.end > source.start and not key in ("contact.email", "contact.phone"):
                    part += 1
                    batches["resume_record_source"].append({
                        **context, "part_no": part, "field_key": key if key in fields else None,
                        "span_no": source.span, "start_offset": source.start, "end_offset": source.end,
                    })
                grouped[source.span].append(source)
            if key:
                for span_no, pieces in grouped.items():
                    start = min(s.start for s in pieces)
                    end = max(s.end for s in pieces)
                    batches["resume_evidence_link"].append({
                        **context, "field_key": key, "span_no": span_no,
                        "start_offset": start if end > start else None,
                        "end_offset": end if end > start else None,
                    })
        for key, (reason, span_no) in record.issues.items():
            batches["resume_field_issue"].append({**context, "field_key": key, "span_no": span_no, "reason": reason})
    for table, rows in batches.items():
        insert(connection, table, rows)


def build_projection(connection, version_id, policy_version=None):
    if policy_version is None:
        policy_version = connection.execute(text("SELECT policy_version FROM search_state FOR UPDATE")).scalar_one()
    rows = connection.execute(text("""
        SELECT p.record_no, substr(s.raw_text,p.start_offset+1,p.end_offset-p.start_offset) AS value
        FROM resume_record_source p
        JOIN resume_source_span s USING(version_id,span_no)
        JOIN field_usage_policy f ON f.field_key=p.field_key AND f.purpose='search'
          AND f.allowed AND f.policy_version=:policy
        JOIN field_definition d ON d.field_key=f.field_key AND d.status='active'
        WHERE p.version_id=:v AND NOT s.is_sensitive AND s.span_kind <> 'photo'
          AND p.start_offset >= 0 AND p.end_offset <= char_length(s.raw_text)
          AND NOT EXISTS (SELECT 1 FROM resume_field_issue i
              WHERE i.version_id=p.version_id AND i.record_no=p.record_no AND i.field_key=p.field_key)
        ORDER BY p.record_no,p.part_no
    """), {"v": version_id, "policy": policy_version}).mappings()
    texts = defaultdict(list)
    for row in rows:
        if row["value"] and "█" not in row["value"]:
            texts[row["record_no"]].append(norm(row["value"]))
    insert(connection, "resume_search_record", [
        {"version_id": version_id, "record_no": key, "policy_version": policy_version, "text_norm": " ".join(parts)}
        for key, parts in texts.items() if any(parts)
    ])


def bump_search(connection):
    connection.execute(text("UPDATE search_state SET search_revision=search_revision+1 WHERE singleton"))


def clone_extraction(connection, old_version, new_version):
    for table, columns in COPY_TABLES.items():
        column_list = ",".join(columns)
        connection.execute(text(
            f"INSERT INTO {table} (version_id,{column_list}) "
            f"SELECT :new,{column_list} FROM {table} WHERE version_id=:old"
        ), {"old": old_version, "new": new_version})


def publish(connection, version_id):
    build_projection(connection, version_id)
    connection.execute(text("""
        UPDATE resume_version SET status='published', extractor_version=:rules, published_at=now(), fail_reason=NULL
        WHERE id=:v
    """), {"v": version_id, "rules": RULE_VERSION})
    connection.execute(text("""
        UPDATE application SET active_version_id=:v,pending_version_id=NULL
        WHERE deleted_at IS NULL AND (pending_version_id=:v
            OR (pending_version_id IS NULL AND active_version_id IS NULL AND submitted_version_id=:v))
    """), {"v": version_id})
    bump_search(connection)
