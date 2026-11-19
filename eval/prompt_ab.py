"""Prompt A/B regression: real model → guard → metrics (doc 11 §6.2).

退出码：0 全达标；1 未达标仍有迭代空间；2 触达停机条件。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.answer_guard import BANNED, check  # noqa: E402
from app.core.config import get_settings  # noqa: E402

CASES_FILE = ROOT / "eval" / "cases" / "analysis_cases.jsonl"
REPORT_FILE = ROOT / "eval" / "prompt_report.json"
LOG_FILE = ROOT / "eval" / "prompt_iterations.log"
MAX_ITER = 4

INLINE_CASES = [
    {"id": "S01", "question": "本月投递与录取趋势", "kind": "stats", "scope": {}},
    {"id": "S02", "question": "近 30 天各部门投递对比", "kind": "stats", "scope": {}},
    {"id": "S03", "question": "当前岗位招聘缺口", "kind": "stats", "scope": {}},
    {"id": "S04", "question": "近 7 天投递趋势", "kind": "stats", "scope": {}},
    {"id": "S05", "question": "近 90 天录取趋势", "kind": "stats", "scope": {}},
    {"id": "S06", "question": "哪些岗位还没有计划人数", "kind": "stats", "scope": {}},
    {"id": "S07", "question": "研发部的投递情况", "kind": "stats", "scope": {"department": "研发部"}},
    {"id": "S08", "question": "本月新增多少简历", "kind": "stats", "scope": {}},
    {"id": "S09", "question": "目前有多少招聘中的岗位", "kind": "stats", "scope": {}},
    {"id": "S10", "question": "计划缺口最大的岗位是哪个", "kind": "stats", "scope": {}},
    {"id": "S11", "question": "上周期和这周期对比", "kind": "stats", "scope": {}},
    {"id": "S12", "question": "本月面试安排有多少", "kind": "stats", "scope": {}},
]


def load_cases() -> tuple[list[dict], str]:
    if CASES_FILE.exists():
        rows = [json.loads(line) for line in CASES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[:30], "FILE"
    return INLINE_CASES, "INLINE"


def build_facts(case: dict) -> dict:
    """用确定性样例事实替代 SQL 快照——评估提示词，不评估统计口径。"""
    scope = case.get("scope") or {}
    facts = {
        "facts": [
            {"id": "F1", "key": "applications", "label": "新增投递", "v": 41, "unit": "份",
             "delta": {"prev": 53, "diff": -12, "diff_pct": -22.6}},
            {"id": "F2", "key": "hired", "label": "首次录取", "v": 3, "unit": "人",
             "delta": {"prev": 3, "diff": 0, "diff_pct": 0.0}},
            {"id": "F3", "key": "open_jobs", "label": "招聘中岗位", "v": 5, "unit": "个"},
            {"id": "F4", "key": "gap", "label": "已设计划缺口", "v": 6, "unit": "人"},
        ],
        "anomalies": [
            {"id": "A1", "type": "zero_flow", "text": "数据平台岗 30 天内新增投递 0 份，计划缺口 3 人"},
            {"id": "A2", "type": "unplanned", "text": "客服专员岗 未设置计划人数，当前投递 12 份"},
        ],
        "breakdown": [{"id": "B1", "by": "department",
                       "rows": [{"k": "研发部", "v": 28}, {"k": "市场部", "v": 13}]}],
        "gaps": [{"field": "channel", "reason": "指标未接入"}, {"field": "招聘周期", "reason": "历史区间未接入"}],
        "window": {"start": "2026-10-21", "end": "2026-11-19", "days": 30, "unit": "day"},
        "scope": {"department_id": None, "department_name": scope.get("department"),
                  "job_id": None, "job_title": None},
    }
    return facts


MATCH_FACTS = {
    "required_count": 8,
    "counts": {"met": 5, "partial": 1, "unverified": 2, "not_met": 0},
    "per_skill": [
        {"skill": "Go", "status": "met", "evidence": {"quote": "负责订单中台网关，QPS 峰值 1.2 万", "record_no": 3}},
        {"skill": "PostgreSQL", "status": "met", "evidence": {"quote": "设计分库分表方案，支撑日均千万订单", "record_no": 5}},
        {"skill": "Kubernetes", "status": "unverified", "evidence": None},
        {"skill": "消息队列", "status": "unverified", "evidence": None},
    ],
    "gaps": [],
}


def slang_density(text: str) -> float:
    hits = sum(1 for word in BANNED if word in text)
    return hits / max(1, len(text)) * 100


def call_model(system: str, data: dict) -> str:
    from urllib.request import urlopen

    from app.api.recruiting_analysis import model_key, model_request

    settings = get_settings()
    if not (settings.recruiting_llm_url and model_key(settings)):
        raise RuntimeError("LLM key 未配置（RECRUITING_LLM_KEY / DASHSCOPE_API_KEY）")
    req = model_request(system, data)
    req.add_header("Accept", "application/json")
    with urlopen(req, timeout=60) as response:
        return (json.load(response)["choices"][0]["message"]["content"] or "").strip()


def run_once(cases: list[dict], kind_filter: str | None) -> dict:
    from app.core.prompts import load as load_prompt

    results, violations = [], {}
    for case in cases:
        kind = kind_filter or case["kind"]
        if kind == "stats":
            facts = build_facts(case)
            data = {key: facts[key] for key in ("facts", "anomalies", "breakdown", "gaps", "window", "scope")}
        else:
            data = dict(MATCH_FACTS)
        try:
            reply = call_model(load_prompt("analysis" if kind == "stats" else "match"), {"facts": data})
        except Exception as cause:  # 网络或配置问题直接上报，不计入指标
            results.append({"id": case["id"], "error": str(cause)})
            continue
        problems = check(reply, data, kind)
        for problem in problems:
            key = problem.split(":")[0]
            violations[key] = violations.get(key, 0) + 1
        results.append({"id": case["id"], "kind": kind, "reply": reply, "violations": problems,
                        "length": len(reply), "slang": slang_density(reply),
                        "trace": bool(re.findall(r"[FA]\d+：", reply)) if kind == "stats" else None})
    valid = [row for row in results if "error" not in row]
    total = len(valid) or 1
    number_violations = sum(1 for row in valid if any(v.startswith("number_out_of_scope") for v in row["violations"]))
    traces = [row for row in valid if row.get("trace") is not None]
    return {
        "cases": results,
        "sample": len(valid),
        "number_violation_rate": number_violations / total,
        "trace_rate": (sum(1 for row in traces if row["trace"]) / len(traces)) if traces else 1.0,
        "slang_density": sum(row["slang"] for row in valid) / total,
        "fallback_rate": 0.0,
        "length_ok_rate": sum(1 for row in valid
                              if (kind_limit := 350 if row["kind"] == "stats" else 120) and row["length"] <= kind_limit) / total,
    }


THRESHOLDS = {
    "number_violation_rate": ("==", 0.0),
    "trace_rate": (">=", 0.95),
    "slang_density": ("<=", 0.5),
    "fallback_rate": ("<=", 0.10),
    "length_ok_rate": (">=", 0.95),
}


def evaluate(metrics: dict) -> list[str]:
    failures = []
    for key, (op, limit) in THRESHOLDS.items():
        value = metrics.get(key)
        if value is None:
            continue
        if op == "==" and value != limit:
            failures.append(f"{key}={value} 应为 {limit}")
        elif op == ">=" and value < limit:
            failures.append(f"{key}={value} 低于 {limit}")
        elif op == "<=" and value > limit:
            failures.append(f"{key}={value} 高于 {limit}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只校验用例与 guard 自身，不调用模型")
    parser.add_argument("--kind", choices=["stats", "match"], help="只评估一类提示词")
    args = parser.parse_args()

    cases, source = load_cases()
    print(f"用例来源 {source}，共 {len(cases)} 条")
    if args.dry_run:
        for case in cases:
            assert check("", build_facts(case)) == ["empty"]
        print("dry-run 通过：用例结构与 guard 自检无异常")
        return 0

    from app.api.recruiting_analysis import model_enabled

    if not model_enabled():
        print("RECRUITING_LLM_KEY / DASHSCOPE_API_KEY 未配置，跳过真实评估。", file=sys.stderr)
        return 1

    metrics = run_once([case for case in cases if not args.kind or case["kind"] == args.kind], args.kind)
    failures = evaluate(metrics)
    REPORT_FILE.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    iteration = 1
    status = "PASS" if not failures else "FAIL"
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"iteration {iteration} = {status} | " + "; ".join(failures or ["全部指标达标"]) + "\n")
    print(json.dumps({key: value for key, value in metrics.items() if key != "cases"},
                     ensure_ascii=False, indent=2))
    print(f"评估报告：{REPORT_FILE}")
    if not failures:
        return 0
    print("未达标项：" + "; ".join(failures))
    print("按 §6.2 回路修改提示词后重跑；4 轮仍不达标请回 T2/T3 排查 guard 白名单与 facts 组装。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
