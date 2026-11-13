"""Application-scoped writes and append-only HR field corrections."""
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from app.core.extraction import CONTACT, degree_value, masked, norm, parse_period
from app.core.resume_pipeline import DETAILS, bump_search, clone_extraction, insert, publish, record_anchor
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


def apply_correction(connection, version_id, record_no, key, value, action, *, replay=False, source_span_no=None):
    params = {"v": version_id, "r": record_no, "key": key}
    kind = connection.execute(text("SELECT kind FROM resume_record WHERE version_id=:v AND record_no=:r"), params).scalar_one_or_none()
    issue = connection.execute(text("""
        SELECT span_no FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key
    """), params).one_or_none()
    if kind is None:
        raise HTTPException(404, "复核事项不存在")
    has_evidence = connection.execute(text("""
        SELECT min(span_no) FROM resume_evidence_link WHERE version_id=:v AND record_no=:r AND field_key=:key
    """), params).scalar_one()
    if not replay and issue is None and has_evidence is None and not (kind == "extension" and key == "extension"):
        raise HTTPException(404, "复核事项不存在")
    if source_span_no is not None and connection.execute(text("""
        SELECT 1 FROM resume_evidence_link WHERE version_id=:v AND record_no=:r AND field_key=:key AND span_no=:span
    """), {**params,"span":source_span_no}).first():
        issue = (source_span_no,)
    elif (issue is None or issue[0] is None) and has_evidence is not None:
        issue = (has_evidence,)
    if kind == "extension" and not connection.execute(text("""
        SELECT 1 FROM resume_extra_value x JOIN resume_source_span s USING(version_id,span_no)
        WHERE x.version_id=:v AND x.record_no=:r AND x.field_key IS NULL AND NOT s.is_sensitive
    """), params).first():
        raise HTTPException(404, "复核事项不存在")
    if kind == "extension":
        value = (value or "").strip()
        if key != "extension" or action == "correct" and (not value or masked(value) != value):
            raise HTTPException(422, "请输入不含受限信息的核实值")
        connection.execute(text("""
            UPDATE resume_extra_value SET reviewed=true,
              payload=CASE WHEN :correct THEN payload || jsonb_build_object('corrected',CAST(:value AS text)) ELSE payload END
            WHERE version_id=:v AND record_no=:r
        """), {**params, "correct": action=="correct", "value":value})
        return
    if action == "dismiss":
        nullable = {
            "profile.name":("resume_profile","name"),"profile.city":("resume_profile","city"),
            "profile.target_role":("resume_profile","target_role"),"education.degree_level":("resume_education","degree_level"),
            "education.major":("resume_education","major"),"work.title":("resume_work","title"),
            "work.employment_type":("resume_work","employment_type"),"work.description":("resume_work","description"),
            "project.role":("resume_project","role"),"project.description":("resume_project","description"),
        }.get(key)
        if kind == "contact":
            connection.execute(text("DELETE FROM resume_contact WHERE version_id=:v AND record_no=:r"), params)
        elif nullable:
            table, column = nullable
            connection.execute(text(f"UPDATE {table} SET {column}=NULL WHERE version_id=:v AND record_no=:r"), params)
        connection.execute(text("DELETE FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        # Unconfirmed extracted data stays excluded from hard screening and publication.
        connection.execute(text("DELETE FROM resume_evidence_link WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        connection.execute(text("DELETE FROM resume_record_source WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        return
    value = (value or "").strip()
    if not value or (kind != "contact" and masked(value) != value):
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
    elif kind == "contact" and column in CONTACT:
        if not CONTACT[column].fullmatch(value):
            raise HTTPException(422, "联系方式格式不正确")
        data = {"type":column,"value":value}
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
    locator = {**locator, "confirmed_from_span": locator.get("confirmed_from_span", issue[0] if issue else None)}
    if kind == "contact":
        connection.execute(text("DELETE FROM resume_field_issue WHERE version_id=:v AND record_no=:r AND field_key=:key"), params)
        return
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
    pending = application["pending_version_id"]
    if old != correction.version_id or old is None or pending and connection.execute(
        text("SELECT status FROM resume_version WHERE id=:v"), {"v":pending}
    ).scalar_one() == "parsing":
        raise HTTPException(409, "简历版本已变化或正在重新解析")
    new = new_version(connection, old, "parsed")
    clone_extraction(connection, old, new)
    connection.execute(text("""
        INSERT INTO resume_card_private(version_id,gender,address)
        SELECT :new,gender,address FROM resume_card_private WHERE version_id=:old
    """), {"new": new, "old": old})
    connection.execute(text("""
        INSERT INTO resume_preview_page(version_id,page_no,object_key,photo_object_key)
        SELECT :new,page_no,object_key,photo_object_key FROM resume_preview_page WHERE version_id=:old
    """), {"new": new, "old": old})
    anchor = ensure_review_anchor(connection, new, correction.record_no)
    apply_correction(connection, new, correction.record_no, correction.field_key, correction.value, correction.action,
                     source_span_no=correction.source_span_no)
    connection.execute(text("""
        INSERT INTO application_field_review(application_id,review_anchor,field_key,action,version_id,record_no)
        VALUES(:a,:anchor,:field,:action,:v,:r)
        ON CONFLICT(application_id,review_anchor,field_key) DO UPDATE
        SET action=excluded.action,version_id=excluded.version_id,record_no=excluded.record_no
    """), {"a":application["id"],"anchor":anchor,"field":correction.field_key,"action":correction.action,
           "v":new,"r":correction.record_no})
    connection.execute(text("UPDATE application SET pending_version_id=:v WHERE id=:a"),
                       {"a": application["id"], "v": new})
    publish(connection, new)
    return new


class ReviewReplayError(Exception):
    pass


def ensure_review_anchor(connection, version, record):
    params = {"v":version,"r":record}
    row = connection.execute(text("""
        SELECT kind,review_anchor FROM resume_record WHERE version_id=:v AND record_no=:r
    """), params).one_or_none()
    if row is None:
        raise HTTPException(404, "复核事项不存在")
    if row.review_anchor:
        return row.review_anchor
    sources = connection.execute(text("""
        SELECT p.span_no,p.start_offset,p.end_offset FROM resume_record_source p
        JOIN resume_source_span s USING(version_id,span_no)
        WHERE p.version_id=:v AND p.record_no=:r AND s.extraction_method<>'manual'
        UNION SELECT p.span_no,p.start_offset,p.end_offset FROM resume_evidence_link p
        JOIN resume_source_span s USING(version_id,span_no)
        WHERE p.version_id=:v AND p.record_no=:r AND s.extraction_method<>'manual' AND p.end_offset>p.start_offset
    """), params).all()
    anchor = record_anchor(row.kind, [tuple(source) for source in sources])
    connection.execute(text("UPDATE resume_record SET review_anchor=:anchor WHERE version_id=:v AND record_no=:r"),
                       {**params,"anchor":anchor})
    return anchor


def replay_reviews(connection, version):
    # A correction created before the review journal existed must never be silently discarded.
    legacy = connection.execute(text("""
        SELECT 1 FROM application a JOIN resume_evidence_link e ON e.version_id=a.active_version_id
        JOIN resume_source_span s USING(version_id,span_no)
        JOIN resume_record r USING(version_id,record_no)
        WHERE a.pending_version_id=:v AND s.extraction_method='manual' AND NOT EXISTS (
            SELECT 1 FROM application_field_review c WHERE c.application_id=a.id
            AND c.field_key=e.field_key AND c.review_anchor=r.review_anchor
        ) LIMIT 1
    """), {"v":version}).first()
    if legacy:
        raise ReviewReplayError()
    entries = connection.execute(text("""
        SELECT r.*,
          CASE WHEN r.field_key='extension' THEN (SELECT payload->>'corrected' FROM resume_extra_value x
                   WHERE x.version_id=r.version_id AND x.record_no=r.record_no)
               WHEN r.field_key IN ('contact.phone','contact.email') THEN (SELECT c.value FROM resume_contact c
                   WHERE c.version_id=r.version_id AND c.record_no=r.record_no)
               ELSE (SELECT s.raw_text FROM resume_evidence_link e JOIN resume_source_span s USING(version_id,span_no)
                   WHERE e.version_id=r.version_id AND e.record_no=r.record_no AND e.field_key=r.field_key
                     AND s.extraction_method='manual' LIMIT 1)
           END AS value
        FROM application_field_review r JOIN application a ON a.id=r.application_id
        WHERE a.pending_version_id=:v ORDER BY r.version_id,r.record_no
    """), {"v":version}).mappings().all()
    for entry in entries:
        candidates = connection.execute(text("""
            SELECT record_no FROM resume_record WHERE version_id=:v AND review_anchor=:anchor LIMIT 2
        """), {"v":version,"anchor":entry["review_anchor"]}).scalars().all()
        if len(candidates)!=1:
            raise ReviewReplayError()
        try:
            apply_correction(connection,version,candidates[0],entry["field_key"],entry["value"],entry["action"],replay=True)
        except HTTPException as exc:
            raise ReviewReplayError() from exc


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
            UNION SELECT p.photo_object_key FROM resume_preview_page p JOIN resume_version v ON v.id=p.version_id
                  JOIN resume_document d ON d.id=v.document_id WHERE d.candidate_id=:c AND p.photo_object_key IS NOT NULL
            ON CONFLICT DO NOTHING
        """), {"c": candidate})
        connection.execute(text("DELETE FROM resume_version USING resume_document d WHERE d.id=document_id AND d.candidate_id=:c"), {"c": candidate})
        connection.execute(text("DELETE FROM resume_document WHERE candidate_id=:c"), {"c": candidate})
        connection.execute(text("DELETE FROM candidate WHERE id=:c AND NOT EXISTS (SELECT 1 FROM candidate x WHERE x.merged_into=:c)"), {"c": candidate})
    audit(connection, actor, "application_purge", row["id"], {"reason": reason})
    bump_search(connection)
