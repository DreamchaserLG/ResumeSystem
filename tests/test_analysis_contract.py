"""Analysis streaming contract: memory, context events, guard/repair loop (doc 11 §附录 E)."""
import json

import pytest

from app.api.recruiting_analysis import AnalysisInput, analysis, local_summary
from app.core.auth import AuthContext


class Auth(AuthContext):
    user_id = 1
    role = "hr"
    departments = [1]
    scope_departments = [1]


def make_auth():
    auth = Auth.__new__(Auth)
    auth.user_id = 1
    auth.role = "hr"
    auth.departments = [1]
    auth.scope_departments = [1]
    return auth


def collect(response):
    import asyncio

    async def drain():
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(drain())
    return [json.loads(chunk.decode() if isinstance(chunk, bytes) else chunk) for chunk in chunks]


def last(events, name):
    return [event for event in events if event["event"] == name][-1]


@pytest.fixture
def fresh_memory(monkeypatch):
    from app.core import conv_memory

    monkeypatch.setattr(conv_memory, "STORE", conv_memory._MemoryStore())
    monkeypatch.setattr(conv_memory, "_store", lambda: None)


@pytest.fixture
def patched_stats(monkeypatch):
    """绕开数据库：stats_snapshot 打桩返回带事实块的报告。"""
    from app.api import recruiting_analysis as module

    report = {"kind": "stats", "start": "2026-10-21", "end": "2026-11-19", "focus": "总览",
              "metrics": {"applications": 41, "hired": 3, "open": 2, "gap": 3, "unplanned": 1, "interviews": 0},
              "trend": [], "departments": [], "jobs": [],
              "window": {"start": "2026-10-21", "end": "2026-11-19", "days": 30, "unit": "day"},
              "scope": {"department_id": 1, "department_name": "研发部", "job_id": None, "job_title": None},
              "facts": [{"id": "F1", "key": "applications", "label": "新增投递", "v": 41, "unit": "份",
                         "delta": {"prev": 53, "diff": -12, "diff_pct": -22.6}},
                        {"id": "F2", "key": "hired", "label": "首次录取", "v": 3, "unit": "人", "delta": {"prev": 3, "diff": 0}}],
              "anomalies": [], "breakdown": [], "gaps": [{"field": "channel", "reason": "指标未接入"}]}
    monkeypatch.setattr(module, "stats_snapshot", lambda request, auth: report)
    monkeypatch.setattr(module, "model_enabled", lambda: True)
    return module, report


def fake_provider(module, replies):
    calls = {"count": 0}

    def fake_urlopen(request, timeout=None):
        import io

        payload = json.loads(request.data.decode())
        if "input" in payload:
            # 原生协议主分析调用：dashscope 原生 SSE 分帧（2 帧思考 + 2 帧正文）
            calls["count"] += 1
            reply = replies[min(calls["count"], len(replies)) - 1]
            frames = ['data:' + json.dumps({"output": {"choices": [{"message": {"content": "", "reasoning_content": "正在核对", "role": "assistant"}, "finish_reason": "null"}]}}, ensure_ascii=False),
                      'data:' + json.dumps({"output": {"choices": [{"message": {"content": "", "reasoning_content": "事实块数字", "role": "assistant"}, "finish_reason": "null"}]}}, ensure_ascii=False)]
            half = max(len(reply) // 2, 1)
            for part in (reply[:half], reply[half:]):
                if part:
                    frames.append('data:' + json.dumps({"output": {"choices": [{"message": {"content": part, "reasoning_content": "", "role": "assistant"}, "finish_reason": "null"}]}}, ensure_ascii=False))
            return io.BytesIO(("\n".join(frames) + "\n").encode())
        # 兼容模式非流式（repair / compress / summary）
        calls["count"] += 1
        reply = replies[min(calls["count"], len(replies)) - 1]
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": reply}}]}).encode())

    module.urlopen = fake_urlopen
    return calls


def test_no_session_no_context(patched_stats, fresh_memory):
    module, report = patched_stats
    fake_provider(module, ["F1：近 30 天新增投递 41 份。\n【数据限制】渠道来源指标未接入。\n【可执行核对】核对编制计划。"])
    events = collect(analysis(AnalysisInput(question="本月投递与录取趋势"), make_auth()))
    assert all(event["event"] != "context" for event in events)
    assert last(events, "done")["source"] == "llm"


def test_first_turn_context(patched_stats, fresh_memory):
    module, report = patched_stats
    fake_provider(module, ["F1：近 30 天新增投递 41 份，较上期减少 12 份。\n【数据限制】渠道来源指标未接入。\n【可执行核对】核对编制计划。"])
    events = collect(analysis(AnalysisInput(question="本月投递与录取趋势", session_id="abc"), make_auth()))
    context = last(events, "context")["context"]
    assert context["turn_no"] == 1
    assert context["scope_source"] == "request"
    assert context["phase"] == "done"
    # 思考帧逐帧下发且先于正文 delta（原生协议 reasoning_effort=high，思考触发随机）
    thinking = [event for event in events if event["event"] == "thinking"]
    assert thinking and "".join(event["text"] for event in thinking) == "正在核对事实块数字"
    assert events.index(thinking[0]) < events.index(last(events, "delta"))


def test_second_turn_uses_memory(patched_stats, fresh_memory):
    module, report = patched_stats
    fake_provider(module, ["F1：近 30 天新增投递 41 份。\n【数据限制】渠道来源指标未接入。\n【可执行核对】核对编制计划。"])
    collect(analysis(AnalysisInput(question="本月投递与录取趋势", session_id="abc"), make_auth()))
    events = collect(analysis(AnalysisInput(question="那趋势呢", session_id="abc"), make_auth()))
    context = last(events, "context")["context"]
    assert context["turn_no"] == 2
    assert context["scope_source"] == "memory"


def test_violation_repairs_then_fallback(patched_stats, fresh_memory):
    module, report = patched_stats
    banned = "F1：整体表现良好，投递 41 份。\n【数据限制】渠道来源指标未接入。\n【可执行核对】核对编制计划。"
    repaired = "F1：新增投递 41 份，较上期减少 12 份。\n【数据限制】渠道来源指标未接入。\n【可执行核对】核对编制计划。"
    calls = fake_provider(module, [banned, repaired])
    events = collect(analysis(AnalysisInput(question="本月投递与录取趋势", session_id="r1"), make_auth()))
    assert last(events, "done")["source"] == "llm"
    # 双调用：主分析 A（思考流）+ B（正文）共 2 次 urlopen，repair 1 次
    assert calls["count"] == 3


def test_double_violation_falls_back_to_rules(patched_stats, fresh_memory):
    module, report = patched_stats
    fake_provider(module, ["F1：整体表现良好，投递 41 份。", "F1：整体表现良好，投递 41 份。"])
    events = collect(analysis(AnalysisInput(question="本月投递与录取趋势", session_id="r2"), make_auth()))
    assert last(events, "done")["source"] == "rules"
    assert last(events, "delta")["text"] == local_summary(report)
