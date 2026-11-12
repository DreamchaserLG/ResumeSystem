"""Application-scoped writes and append-only HR field corrections."""
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from app.core.extraction import degree_value, masked, norm, parse_period
from app.core.resume_pipeline import DETAILS, bump_search, clone_extraction, insert, publish
from app.core.storage import storage_root


def audit(connection, user, action, target, detail=None, target_type="application"):
    insert(connection, "audit_log", [{"actor_id": user, "action": action, "target_type": target_type,
                                      "target_id": str(target), "detail": json.dumps(detail or {}, ensure_ascii=False)}])


def fresh_departments(connection, auth):
    version = connection.execute(text("""
        SELECT perm_version FROM app_user WHERE id=:u AND status='active' FOR SHARE
    """), {"u": auth.user_id}).scalar_one_or_none()
    if version != auth.perm_version:
        raise HTTPException(401, "权限已变化，请重新登录")
    return connection.execute(text("""
        SELECT a.department_id FROM department_admin a JOIN department d ON d.id=a.department_id
        WHERE a.user_id=:u AND d.status='active'
    """), {"u": auth.user_id}).scalars().all()


def application_lock(connection, application_id, auth):
    departments = fresh_departments(connection, auth)
    row = connection.execute(text("""
        SELECT * FROM application WHERE id=:a AND deleted_at IS NULL
          AND handling_department_id=ANY(CAST(:deps AS bigint[])) FOR UPDATE
    """), {"a": application_id, "deps": departments}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "申请不存在")
    return row


def new_version(connection, source_id, status):
    old = connection.execute(text("SELECT * FROM resume_version WHERE id=:id"), {"id": source_id}).mappings().one()
    connection.execute(text("SELECT id FROM resume_document WHERE id=:id FOR UPDATE"), {"id": old["document_id"]})
    return connection.execute(text("""
        INSERT INTO resume_version(document_id, version_no, parser_version, status, preview_key, avatar_key)
        SELECT :document, coalesce(max(version_no),0)+1,:parser,:status,:preview,:avatar
        FROM resume_version WHERE document_id=:document RETURNING id
    """), {"document": old["document_id"], "parser": old["parser_version"], "status": status,
           "preview": old["preview_key"] if status == "parsed" else None,
           "avatar": old["avatar_key"] if status == "parsed" else None}).scalar_one()


def schedule_parse(version_id):
    from app.core.db import owner_engine
    from worker.tasks.parse import process_resume

    try:
        process_resume.apply_async(args=[version_id], retry=False)
    except Exception as error:
        with owner_engine.begin() as connection:
            connection.execute(text("""
                UPDATE resume_version SET status='failed',fail_reason='解析队列暂不可用，可手动重试'
                WHERE id=:v AND status='parsing'
            """), {"v": version_id})
        raise HTTPException(503, "申请已保存，解析队列暂不可用，可从申请列表重试") from error


def apply_correction(connection, version_id, record_no, key, value, action):
    params = {"v": version_id, "r": record_no, "key": key}
    kind = connection.execute(text("SELECT kind FROM resume_record WHERE version_id=:v AND record_no=:r"), params).scalar_one_or_none()
    issue = connection.execute(text("""
        SELECT span_no FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key
    """), params).one_or_none()
    if kind is None or issue is None and not (kind == "extension" and key == "extension"):
        raise HTTPException(404, "复核事项不存在")
    if kind == "extension":
        if action != "dismiss":
            raise HTTPException(422, "扩展字段可确认保留原值")
        connection.execute(text("UPDATE resume_extra_value SET reviewed=true WHERE version_id=:v AND record_no=:r"), params)
        return
    if action == "dismiss":
        connection.execute(text("DELETE FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        # Unconfirmed extracted data stays excluded from hard screening and publication.
        connection.execute(text("DELETE FROM resume_evidence_link WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        connection.execute(text("DELETE FROM resume_record_source WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        return
    value = (value or "").strip()
    if not value or masked(value) != value:
        raise HTTPException(422, "请填写可核实的业务字段")
    column = key.partition(".")[2]
    if kind not in DETAILS or key.split(".")[0] != kind or column in {"photo_present", "school_id", "parent_record_no"}:
        raise HTTPException(422, "此字段只能保留待核实信息")
    table, columns = DETAILS[kind]
    if column == "period" and kind in {"education", "work", "project"}:
        data = parse_period(value)
        if data is None or kind == "education" and data["is_current"]:
            raise HTTPException(422, "请填写有效的起止日期，例如 2021.03–2024.06")
        data = {k: v for k, v in data.items() if k in columns}
    elif key == "education.degree_level":
        degree = degree_value(value)
        if degree is None:
            raise HTTPException(422, "无法识别学历")
        data = {column: degree}
    elif key == "work.employment_type":
        if value not in {"full_time", "part_time", "internship", "contract", "other"}:
            raise HTTPException(422, "请选择有效的雇佣类型")
        data = {column: value}
    elif key == "education.school_raw":
        matches = connection.execute(text("""
            SELECT id FROM school WHERE lower(canonical_name)=:n
            UNION SELECT school_id FROM school_alias WHERE alias_normalized=:n
        """), {"n": norm(value)}).scalars().all()
        if len(matches) > 1:
            raise HTTPException(422, "学校别名存在歧义，请填写全称")
        data = {column: value, "school_id": matches[0] if matches else None}
    elif key == "skill.name":
        skill = connection.execute(text("""
            SELECT id FROM skill WHERE lower(name)=:n UNION SELECT skill_id FROM skill_alias WHERE alias_normalized=:n
        """), {"n": norm(value)}).scalar_one_or_none()
        if skill is None:
            raise HTTPException(422, "请填写技能词典中的名称")
        data = {"skill_id": skill}
    elif column in columns and kind not in {"contact", "skill"}:
        data = {column: value}
    else:
        raise HTTPException(422, "此字段不支持直接更正")
    update = ",".join(f"{col}=:{col}" for col in data)
    changed = connection.execute(text(f"UPDATE {table} SET {update} WHERE version_id=:v AND record_no=:r"),
                                 {**data, **params}).rowcount
    if not changed:
        mandatory = {"work": "company", "education": "school_raw", "project": "project_name", "skill": "skill_id"}.get(kind)
        if mandatory and mandatory not in data:
            raise HTTPException(422, "请先确认该条目的主体")
        insert(connection, table, [{"version_id": version_id, "record_no": record_no, **data}])
    locator = {}
    if issue and issue[0] is not None:
        locator = connection.execute(text("SELECT locator FROM resume_source_span WHERE version_id=:v AND span_no=:s"),
                                     {"v": version_id, "s": issue[0]}).scalar_one_or_none() or {}
    locator = {**locator, "confirmed_from_span": issue[0] if issue else None}
    span_no = connection.execute(text("""
        INSERT INTO resume_source_span(version_id,span_no,raw_text,locator,extraction_method,reading_order)
        SELECT :v,coalesce(max(span_no),0)+1,:value,CAST(:location AS jsonb),'manual',coalesce(max(reading_order),0)+1
        FROM resume_source_span WHERE version_id=:v RETURNING span_no
    """), {"v": version_id, "value": value, "location": json.dumps(locator)}).scalar_one()
    connection.execute(text("DELETE FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
    connection.execute(text("DELETE FROM resume_evidence_link WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
    connection.execute(text("DELETE FROM resume_record_source WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
    source = {"version_id": version_id, "record_no": record_no, "field_key": key, "span_no": span_no,
              "start_offset": 0, "end_offset": len(value)}
    insert(connection, "resume_evidence_link", [source])
    part = connection.execute(text("""
        SELECT coalesce(max(part_no),0)+1 FROM resume_record_source WHERE version_id=:v AND record_no=:r
    """), params).scalar_one()
    insert(connection, "resume_record_source", [{**source, "part_no": part}])


def corrected_version(connection, application, correction):
    old = application["active_version_id"]
    if old != correction.version_id or old is None or application["pending_version_id"] is not None:
        raise HTTPException(409, "简历版本已变化或正在重新解析")
    new = new_version(connection, old, "parsed")
    clone_extraction(connection, old, new)
    connection.execute(text("""
        INSERT INTO resume_card_private(version_id,gender,address)
        SELECT :new,gender,address FROM resume_card_private WHERE version_id=:old
    """), {"new": new, "old": old})
    connection.execute(text("""
        INSERT INTO resume_preview_page(version_id,page_no,object_key)
        SELECT :new,page_no,object_key FROM resume_preview_page WHERE version_id=:old
    """), {"new": new, "old": old})
    apply_correction(connection, new, correction.record_no, correction.field_key, correction.value, correction.action)
    connection.execute(text("UPDATE application SET pending_version_id=:v WHERE id=:a"),
                       {"a": application["id"], "v": new})
    publish(connection, new)
    return new


def cleanup_files(connection):
    pending = connection.execute(text("SELECT object_key FROM resume_file_cleanup FOR UPDATE SKIP LOCKED")).scalars().all()
    root = Path(storage_root()).resolve()
    remaining = 0
    for key in pending:
        path = (root / key).resolve()
        if not path.is_relative_to(root) or path == root:
            remaining += 1
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            remaining += 1
            continue
        connection.execute(text("DELETE FROM resume_file_cleanup WHERE object_key=:k"), {"k": key})
    return remaining


def purge_application(connection, row, actor, reason):
    candidate = row["candidate_id"]
    connection.execute(text("SELECT id FROM candidate WHERE id=:c FOR UPDATE"), {"c": candidate})
    connection.execute(text("DELETE FROM application WHERE id=:a"), {"a": row["id"]})
    if not connection.execute(text("SELECT 1 FROM application WHERE candidate_id=:c LIMIT 1"), {"c": candidate}).first():
        connection.execute(text("""
            INSERT INTO resume_file_cleanup(object_key)
            SELECT object_key FROM resume_document WHERE candidate_id=:c
            UNION SELECT preview_key FROM resume_document WHERE candidate_id=:c AND preview_key IS NOT NULL
            UNION SELECT v.preview_key FROM resume_version v JOIN resume_document d ON d.id=v.document_id
                  WHERE d.candidate_id=:c AND v.preview_key IS NOT NULL
            UNION SELECT v.avatar_key FROM resume_version v JOIN resume_document d ON d.id=v.document_id
                  WHERE d.candidate_id=:c AND v.avatar_key IS NOT NULL
            UNION SELECT p.object_key FROM resume_preview_page p JOIN resume_version v ON v.id=p.version_id
                  JOIN resume_document d ON d.id=v.document_id WHERE d.candidate_id=:c
            ON CONFLICT DO NOTHING
        """), {"c": candidate})
        connection.execute(text("DELETE FROM resume_version USING resume_document d WHERE d.id=document_id AND d.candidate_id=:c"), {"c": candidate})
        connection.execute(text("DELETE FROM resume_document WHERE candidate_id=:c"), {"c": candidate})
        connection.execute(text("DELETE FROM candidate WHERE id=:c AND NOT EXISTS (SELECT 1 FROM candidate x WHERE x.merged_into=:c)"), {"c": candidate})
    audit(connection, actor, "application_purge", row["id"], {"reason": reason})
    bump_search(connection)
