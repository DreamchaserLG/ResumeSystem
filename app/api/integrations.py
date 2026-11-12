"""M7 integration API for application lists, versioned facts, and evidence."""
import base64
import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text

from app.core.db import rls_session
from app.core.integration import IntegrationScope, authenticate, read_scope, reserve_request, unauthorized
from app.core.integration_export import FILTERS, SCOPE, application_summary, export_detail, find_application, find_applications, scope_params

router = APIRouter(prefix="/applications", tags=["integrations"])
bearer = HTTPBearer(auto_error=False, scheme_name="IntegrationBearer")
MAX_ID = 2**63 - 1
Identifier = Annotated[int, Path(gt=0, le=MAX_ID)]
VersionQuery = Annotated[int | None, Query(gt=0, le=MAX_ID)]
FilterTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int | None = Field(default=None, gt=0, le=MAX_ID)
    cursor: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=20, gt=0, le=100)
    degree_level: Literal["phd", "master", "bachelor", "associate", "high_school", "other"] | None = None
    school: FilterTerm | None = None
    company: FilterTerm | None = None
    city: FilterTerm | None = None
    skill: FilterTerm | None = None


def _audit(session, scope: IntegrationScope, action: str, ids: list[int], fields: set[str], **details) -> None:
    payload = {"client_id": scope.client_id, "application_ids": ids, "field_keys": sorted(fields),
               "policy_version": scope.policy_version, **details}
    session.execute(text("""
        INSERT INTO audit_log (action, target_type, target_id, detail)
        VALUES (:action, 'integration_client', :target, CAST(:detail AS jsonb))
    """), {"action": action, "target": str(scope.client_id), "detail": json.dumps(payload)})


def _authorize(request: Request, response: Response, credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> IntegrationScope:
    if credential is None:
        raise unauthorized()
    scope = authenticate(credential.credentials)
    remaining, retry_after, audit = reserve_request(scope)
    headers = {"X-RateLimit-Remaining": str(remaining), "X-Policy-Version": str(scope.policy_version),
               "Cache-Control": "private, no-store"}
    if retry_after:
        if audit:
            with rls_session(client_id=scope.client_id) as session:
                _audit(session, scope, "integration_rate_limited", [], set(), retry_after=retry_after, operation=request.scope["route"].name)
        raise HTTPException(429, "集成账号配额已用完", headers={**headers, "Retry-After": str(retry_after)})
    response.headers.update(headers)
    return scope


Authorized = Annotated[IntegrationScope, Depends(_authorize)]


def _not_found() -> HTTPException:
    return HTTPException(404, "申请、版本或证据不存在")


def _cursor_scope(scope: IntegrationScope, filters: dict, limit: int) -> str:
    data = json.dumps([scope.client_id, scope.perm_version, scope.policy_version, scope.jobs, filters, limit],
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()[:24]


def _decode_cursor(value: str | None, fingerprint: str) -> int:
    if value is None:
        return 0
    try:
        data = base64.b64decode(value, altchars=b"-_", validate=True).decode("ascii")
        scope, position = data.split(":")
        if scope != fingerprint or not position.isascii() or not position.isdigit():
            raise ValueError
        after = int(position)
        if not 0 < after <= MAX_ID:
            raise ValueError
        return after
    except ValueError as error:
        raise HTTPException(422, "游标无效或查询范围已改变") from error


def _encode_cursor(position: int, fingerprint: str) -> str:
    return base64.urlsafe_b64encode(f"{fingerprint}:{position}".encode()).decode()


def _before_delivery(scope: IntegrationScope, rows: list, action: str, fields: set[str],
                     historical_version: int | None = None, **details) -> None:
    """Recheck authorization in a fresh transaction after reading and assembling the payload."""
    with rls_session(client_id=scope.client_id) as session:
        fresh = read_scope(session, scope.digest)
        if fresh is None:
            raise unauthorized()
        ids = [row["application_id"] for row in rows]
        if ids and historical_version is not None:
            if find_application(session, fresh, ids[0], historical_version) is None:
                raise _not_found()
        elif ids:
            found = session.execute(text(f"""
                SELECT a.id, a.active_version_id FROM application a
                WHERE a.id = ANY(CAST(:ids AS bigint[])) AND {SCOPE}
            """), {**scope_params(fresh), "ids": ids}).all()
            current = dict((application, version) for application, version in found)
            if any(application not in current for application in ids):
                raise _not_found()
            if any(current[row["application_id"]] != row["version_id"] for row in rows):
                raise HTTPException(409, "简历版本已变更，请重新请求")
        if fresh != scope:
            raise HTTPException(409, "授权或字段策略已变更，请重新请求")
        _audit(session, scope, action, ids, fields, **details)


@router.get("")
def list_applications(query: Annotated[ListQuery, Query()], scope: Authorized):
    filters = query.model_dump(exclude={"cursor", "limit"}, exclude_none=True)
    for name, (key, _, _) in FILTERS.items():
        if name in filters and key not in scope.filter_fields:
            raise HTTPException(403, "筛选字段未获授权")
    if query.job_id is not None and query.job_id not in scope.job_ids:
        raise _not_found()
    fingerprint = _cursor_scope(scope, filters, query.limit)
    after = _decode_cursor(query.cursor, fingerprint)
    with rls_session(client_id=scope.client_id) as session:
        rows = find_applications(session, scope, filters, after, query.limit + 1)
    page = rows[:query.limit]
    next_cursor = _encode_cursor(page[-1]["application_id"], fingerprint) if len(rows) > query.limit else None
    audit_action = "integration_bulk_listing" if query.cursor and query.job_id is None and len(scope.jobs) > 1 else "integration_list"
    _before_delivery(scope, page, audit_action, {FILTERS[key][0] for key in filters if key in FILTERS},
                     cursor_used=query.cursor is not None, job_id=query.job_id, scope_job_count=len(scope.jobs))
    return {"items": [application_summary(row) for row in page], "next_cursor": next_cursor,
            "schema_version": "1.0.0"}


@router.get("/{application_id}")
def get_application(application_id: Identifier, scope: Authorized, version_id: VersionQuery = None):
    with rls_session(client_id=scope.client_id) as session:
        row = find_application(session, scope, application_id, version_id)
        if row is None:
            raise _not_found()
        body, sources = export_detail(session, scope, row)
    _before_delivery(scope, [row], "integration_detail", {s.field_key for s in sources},
                     historical_version=version_id, version_id=row["version_id"])
    return body


@router.get("/{application_id}/evidence/{span_no}")
def get_evidence(application_id: Identifier, span_no: Identifier, scope: Authorized,
                 version_id: Annotated[int, Query(gt=0, le=MAX_ID)]):
    with rls_session(client_id=scope.client_id) as session:
        row = find_application(session, scope, application_id, version_id)
        if row is None:
            raise _not_found()
        _, sources = export_detail(session, scope, row, span_no)
    matched = [source for source in sources if source.span_no == span_no]
    if not matched:
        raise _not_found()
    _before_delivery(scope, [row], "integration_evidence", {s.field_key for s in matched},
                     historical_version=version_id, version_id=version_id, span_no=span_no)
    return {"application_id": application_id, "version_id": version_id, "span_no": span_no,
            "locator": matched[0].locator, "extraction_method": matched[0].method,
            "excerpts": [{"field_key": s.field_key, "text": s.excerpt,
                          "start_offset": s.start, "end_offset": s.end} for s in matched]}
