"""Conversation memory: window, compression, scope resolution (doc 11 §附录 E)."""
import json

import pytest

from app.core import conv_memory
from app.core.conv_memory import _MemoryStore, append_turn, load, maybe_compress, resolve_scope, save


@pytest.fixture
def store(monkeypatch):
    fresh = _MemoryStore()
    monkeypatch.setattr(conv_memory, "STORE", fresh)
    return fresh


class Auth:
    role = "hr"
    departments = [3]


def test_append_turn_increments(store):
    ctx = load(1, "s1")
    append_turn(ctx, "问题一", "答一", ["F1"], "stats")
    assert ctx["turn_no"] == 1
    append_turn(ctx, "问题二", "答二", ["F2"], "stats")
    assert ctx["turn_no"] == 2


def test_window_compresses_to_three(store):
    ctx = load(1, "s2")
    for index in range(5):
        append_turn(ctx, f"问题{index}", "答", [], "stats")
    maybe_compress(ctx, "问题4", None)
    assert len(ctx["window"]) <= conv_memory.WINDOW_KEEP


def test_compression_has_no_numbers(store):
    def llm(history):
        return json.dumps({"scope": {"department_id": None, "department_name": None,
                                    "job_id": None, "job_title": "高级后端工程师", "window": None},
                           "focus": ["trend"], "open_items": ["缺口 3 人怎么办"], "last_answer_kind": "stats"},
                          ensure_ascii=False)

    ctx = load(1, "s3")
    append_turn(ctx, "问题", "答", [], "stats")
    maybe_compress(ctx, "问题", llm)
    assert "3" not in json.dumps(ctx["state"].get("open_items", []), ensure_ascii=False)


def test_invalid_json_rolls_back(store):
    ctx = load(1, "s4")
    append_turn(ctx, "问题", "答", [], "stats")
    ctx["state"] = {"focus": ["trend"], "open_items": ["旧条目"]}
    ctx = maybe_compress(ctx, "问题", lambda history: "这不是 JSON")
    assert ctx["state"]["open_items"] == ["旧条目"]


def test_redis_unavailable_falls_back(store, monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(conv_memory, "get_redis", broken)
    ctx = load(1, "s5")
    append_turn(ctx, "问题", "答", [], "stats")
    save(1, "s5", ctx)
    again = load(1, "s5")
    assert again["turn_no"] == 1


def test_resolve_scope_strips_unauthorized_department(store):
    state = {"scope": {"department_id": 9}}
    resolved = resolve_scope(state, Auth())
    assert resolved["department_id"] is None
    resolved = resolve_scope({"scope": {"department_id": 3}}, Auth())
    assert resolved["department_id"] == 3
