"""Conversation memory: sliding window + overwriting summary, stored in Redis (TTL 2h).

Redis 不可用时静默降级为无记忆——不抛错、不阻塞查询。
"""
import json
import logging
import re
from typing import Any, Callable

from app.core.cache import get_redis

logger = logging.getLogger(__name__)

WINDOW_KEEP = 3
TTL_SECONDS = 7200
Ctx = dict  # {"session_id","turn_no","state","window","summary"}


class _MemoryStore:
    """进程内兜底存储，供测试与 Redis 降级使用；模块级 STORE 可替换。"""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = value


STORE = _MemoryStore()


def _store():
    try:
        client = get_redis()
        client.ping()
        return client
    except Exception:  # Redis 未起 / 连接失败：静默降级
        return None


def _key(user_id: int, session_id: str) -> str:
    return f"conv:{user_id}:{session_id}"


def load(user_id: int, session_id: str) -> Ctx:
    client = _store()
    raw = client.get(_key(user_id, session_id)) if client else STORE.get(_key(user_id, session_id))
    if not raw:
        return {"session_id": session_id, "turn_no": 0, "state": {}, "window": [], "summary": False}
    try:
        ctx = json.loads(raw)
        ctx.setdefault("turn_no", 0)
        ctx.setdefault("state", {})
        ctx.setdefault("window", [])
        ctx.setdefault("summary", False)
        return ctx
    except (ValueError, TypeError):
        logger.warning("Conversation memory corrupted; starting a fresh context")
        return {"session_id": session_id, "turn_no": 0, "state": {}, "window": [], "summary": False}


def save(user_id: int, session_id: str, ctx: Ctx) -> None:
    payload = json.dumps(ctx, ensure_ascii=False)
    client = _store()
    if client:
        try:
            client.set(_key(user_id, session_id), payload, ex=TTL_SECONDS)
            return
        except Exception:
            logger.warning("Conversation memory store failed; keeping memory only in process")
    STORE.set(_key(user_id, session_id), payload, ex=TTL_SECONDS)


def append_turn(ctx: Ctx, question: str, answer_lite: str, fact_ids: list[str], kind: str) -> Ctx:
    ctx["turn_no"] = int(ctx.get("turn_no", 0)) + 1
    window = list(ctx.get("window", []))
    window.append({"q": question, "a": answer_lite, "fact_ids": fact_ids, "kind": kind})
    ctx["window"] = window[-WINDOW_KEEP:]
    return ctx


_DATE_RULE = re.compile(r"近\s*(\d+)\s*天|本月|上月")


def _extract_slots(ctx: Ctx, question: str, job_titles: list[str]) -> dict:
    """规则先行：只有 open_items 留给 LLM，slot 全部规则抽取，记忆不存数字。"""
    state = dict(ctx.get("state") or {})
    scope = dict(state.get("scope") or {})
    for name in scope.get("department_name") or []:
        pass  # department_name 由调用方按 auth 可见部门回填，不在问题文本里猜
    if _DATE_RULE.search(question):
        scope["window_text"] = _DATE_RULE.search(question).group(0)
    for title in job_titles:
        if title and title in question:
            scope["job_title"] = title
            break
    focus = state.get("focus") or []
    if re.search(r"匹配|找候选|推荐简历", question) and "match" not in focus:
        focus = ([*focus, "match"])[-3:]
    state["focus"], state["scope"] = focus, scope
    state.setdefault("open_items", [])
    return state


def maybe_compress(ctx: Ctx, question: str, llm_compress: Callable[[str], str] | None,
                   job_titles: list[str] | None = None) -> Ctx:
    """窗口超限或摘要缺失时重建状态；open_items 由 LLM 补全，失败回滚旧 state。"""
    job_titles = job_titles or []
    needs = int(ctx.get("turn_no", 0)) > WINDOW_KEEP or not ctx.get("state")
    if not needs:
        return ctx
    old_state = dict(ctx.get("state") or {})
    state = _extract_slots({"state": old_state}, question, job_titles)
    if llm_compress is None:
        ctx["state"] = state
        return ctx
    history = "\n".join(f"问：{turn['q']}\n答：{turn['a']}" for turn in ctx.get("window", []))
    try:
        raw = llm_compress(history)
        cleaned = raw.strip()
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", cleaned, flags=re.I)
        parsed = json.loads(cleaned)
        open_items = [item for item in parsed.get("open_items", []) if isinstance(item, str)][:3]
        if any(_DATE_RULE_NUM.search(item) for item in open_items):
            raise ValueError("memory must not contain numbers")
        state["open_items"] = [item[:20] for item in open_items]
        ctx["summary"] = True
    except Exception as cause:
        logger.warning("Conversation compress failed; keep rule-based state (%s)", cause)
        state["open_items"] = old_state.get("open_items", [])
        ctx["summary"] = bool(old_state)
    ctx["state"] = state
    return ctx


_DATE_RULE_NUM = re.compile(r"\d")


def scope_echo(state: dict) -> str:
    scope = (state or {}).get("scope") or {}
    parts = []
    if scope.get("department_name"):
        parts.append(scope["department_name"])
    elif scope.get("department_id"):
        parts.append("部门 #" + str(scope["department_id"]))
    text = state.get("scope_window_text") or scope.get("window_text")
    if text:
        parts.append(text)
    if scope.get("job_title"):
        parts.append(scope["job_title"])
    if scope.get("window"):
        parts.append(scope["window"]["start"] + "~" + scope["window"]["end"])
    return " / ".join(parts) if parts else ""


def resolve_scope(state: dict, auth, asked_department=None, asked_job=None) -> dict:
    """把记忆口径回填为查询参数；department_id 必须重新过一遍授权部门。"""
    scope = (state or {}).get("scope") or {}
    department = asked_department or scope.get("department_id")
    if department and department not in auth.departments and auth.role != "super_admin":
        department = None
    return {"department_id": department, "job_id": asked_job or scope.get("job_id")}


def department_for_state(ctx: Ctx, name: str | None, department_id: int | None = None) -> None:
    """调用方在拿到部门名后回填 id 与名称（保持记忆里可读的 scope_echo）。"""
    state = dict(ctx.get("state") or {})
    scope = dict(state.get("scope") or {})
    scope["department_name"] = name or None
    if department_id:
        scope["department_id"] = department_id
    elif name is None:
        scope.pop("department_id", None)
    state["scope"] = scope
    ctx["state"] = state
