"""Bearer credential lookup, authorization snapshots and request quotas for integrations."""
from dataclasses import dataclass
import hashlib
import math
import re
from time import time

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import get_redis
from app.core.db import SessionLocal

KEY_PATTERN = re.compile(r"rsint_[A-Za-z0-9_-]{43}")
PRIVATE_FIELD = re.compile(
    r"(?:^contact[.]|photo|passport|identity|national_id|id_card|marital|pregnan|fertility|gender|"
    r"\bsex\b|birth_date|婚育|婚姻|证件|身份证|性别|照片|头像)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntegrationScope:
    client_id: int
    digest: str
    perm_version: int
    policy_version: int
    jobs: tuple[tuple[int, int | None], ...]
    export_fields: frozenset[str]
    filter_fields: frozenset[str]
    requests_per_second: int
    requests_per_day: int

    @property
    def job_ids(self) -> list[int]:
        return [job for job, _ in self.jobs]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def read_scope(session: Session, digest: str) -> IntegrationScope | None:
    row = session.execute(text("""
        SELECT c.id, c.perm_version, st.policy_version, c.requests_per_second, c.requests_per_day,
               (SELECT jsonb_agg(jsonb_build_array(j.job_id, j.handling_department_id) ORDER BY j.job_id)
                FROM integration_job j WHERE j.client_id = c.id) AS jobs,
               (SELECT jsonb_agg(jsonb_build_array(p.field_key, p.purpose))
                FROM field_usage_policy p JOIN field_definition f USING (field_key)
                WHERE p.policy_version = st.policy_version AND p.allowed AND f.status = 'active'
                  AND p.purpose IN ('filter', 'export') AND p.field_key = ANY(c.allowed_fields)) AS fields
        FROM integration_client c CROSS JOIN search_state st
        WHERE c.id = authenticate_integration_key(:digest) AND st.singleton
    """), {"digest": digest}).mappings().one_or_none()
    if row is None:
        return None
    allowed = [(key, purpose) for key, purpose in (row["fields"] or []) if not PRIVATE_FIELD.search(key)]
    exported = frozenset(key for key, purpose in allowed if purpose == "export")
    return IntegrationScope(
        client_id=row["id"], digest=digest, perm_version=row["perm_version"],
        policy_version=row["policy_version"], jobs=tuple(tuple(job) for job in (row["jobs"] or [])),
        export_fields=exported,
        filter_fields=frozenset(key for key, purpose in allowed if purpose == "filter" and key in exported),
        requests_per_second=row["requests_per_second"], requests_per_day=row["requests_per_day"],
    )


def authenticate(token: str) -> IntegrationScope:
    if not KEY_PATTERN.fullmatch(token):
        raise unauthorized()
    with SessionLocal() as session:
        scope = read_scope(session, token_digest(token))
    if scope is None:
        raise unauthorized()
    return scope


def unauthorized() -> HTTPException:
    return HTTPException(401, "集成账号凭证无效", headers={"WWW-Authenticate": "Bearer"})


# Check both fixed windows before admitting the request. Expirations and increments are atomic.
QUOTA_SCRIPT = """
local second = tonumber(redis.call('GET', KEYS[1]) or '0')
local daily = tonumber(redis.call('GET', KEYS[2]) or '0')
local second_limit = tonumber(ARGV[1])
local daily_limit = tonumber(ARGV[2])
if daily >= daily_limit then
    local first = redis.call('SET', KEYS[2] .. ':alert', '1', 'NX', 'EX', ARGV[4])
    return {2, 0, first and 1 or 0}
end
if second >= second_limit then
    local first = redis.call('SET', KEYS[1] .. ':alert', '1', 'NX', 'EX', ARGV[3])
    return {1, 0, first and 1 or 0}
end
if redis.call('INCR', KEYS[1]) == 1 then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
if redis.call('INCR', KEYS[2]) == 1 then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
return {0, daily_limit - daily - 1, 0}
"""


def reserve_request(scope: IntegrationScope, now: float | None = None) -> tuple[int, int, bool]:
    now = time() if now is None else now
    second = int(now)
    day = second // 86400
    daily_retry = math.ceil((day + 1) * 86400 - now)
    prefix = f"rs:integration:quota:{{{scope.client_id}}}"
    try:
        limited, remaining, audit = get_redis().eval(
            QUOTA_SCRIPT, 2, f"{prefix}:second:{second}", f"{prefix}:day:{day}",
            scope.requests_per_second, scope.requests_per_day, 2, daily_retry + 1,
        )
    except RedisError as error:
        raise HTTPException(503, "集成 API 配额服务暂不可用") from error
    retry_after = {0: 0, 1: 1, 2: daily_retry}[limited]
    return int(remaining), retry_after, bool(audit)
