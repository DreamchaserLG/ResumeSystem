"""Guard checks: numbers outside facts, banned phrases, traceability (doc 11 §附录 E)."""
from app.core.answer_guard import check

STATS_FACTS = {
    "facts": [
        {"id": "F1", "key": "applications", "label": "新增投递", "v": 41, "unit": "份",
         "delta": {"prev": 53, "diff": -12, "diff_pct": -22.6}},
        {"id": "F2", "key": "hired", "label": "首次录取", "v": 3, "unit": "人", "delta": {"prev": 3, "diff": 0}},
    ],
    "anomalies": [{"id": "A1", "type": "zero_flow", "text": "数据平台岗 30 天内新增投递 0 份，计划缺口 3 人"}],
    "gaps": [{"field": "channel", "reason": "指标未接入"}],
    "window": {"start": "2026-10-21", "end": "2026-11-19", "days": 30, "unit": "day"},
}


def test_empty_text():
    assert "empty" in check("", STATS_FACTS)


def test_banned_phrase():
    text = "F1：新增投递 41 份。整体表现良好，建议重点关注。"
    problems = check(text, STATS_FACTS)
    assert any(problem.startswith("banned_phrase") for problem in problems)


def test_number_out_of_scope():
    text = "F1：新增投递 37 份。"
    problems = check(text, STATS_FACTS)
    assert any(problem.startswith("number_out_of_scope") for problem in problems)


def test_match_counts_and_verbatim_quote():
    facts = {
        "required_count": 8,
        "counts": {"met": 5, "partial": 1, "unverified": 2, "not_met": 0},
        "per_skill": [
            {"skill": "Go", "status": "met",
             "evidence": {"quote": "负责订单中台网关，QPS 峰值 1.2 万", "record_no": 3}},
            {"skill": "Kubernetes", "status": "unverified", "evidence": None},
        ],
    }
    text = "核对 8 项要求：5 项有证据，1 项部分匹配，2 项待核实，0 项不满足。\nGo：负责订单中台网关，QPS 峰值 1.2 万。"
    assert check(text, facts, "match") == []


def test_stats_no_fact_id():
    text = "新增投递 41 份，首次录取 3 人。"
    assert any(problem.startswith("no_fact_id") for problem in check(text, STATS_FACTS))


def test_unknown_fact_id_keeps_number_whitelisted():
    text = "F9：投递 41 份。"
    problems = check(text, STATS_FACTS)
    assert any(problem.startswith("unknown_fact_id") for problem in problems)
    assert not any(problem.startswith("number_out_of_scope") for problem in problems)


def test_duplicate_fact_id():
    text = "F1：投递 41 份。\nF1：录取 3 人。"
    assert "duplicate_fact_id" in check(text, STATS_FACTS)


def test_stats_too_long():
    text = "F1：" + "很长的陈述" * 80
    assert any(problem.startswith("too_long") for problem in check(text, STATS_FACTS))


def test_match_score_or_grade():
    facts = {"required_count": 1, "counts": {"met": 1, "partial": 0, "unverified": 0, "not_met": 0},
             "per_skill": [{"skill": "Go", "status": "met",
                            "evidence": {"quote": "负责订单中台网关服务", "record_no": 1}}]}
    text = "核对 1 项要求：1 项有证据，0 项部分匹配，0 项待核实，0 项不满足。\n综合得分 85 分\nGo：负责订单中台网关服务。"
    assert any(problem.startswith("score_or_grade") for problem in check(text, facts, "match"))


def test_valid_stats_text():
    text = ("F1：近 30 天新增投递 41 份，较上期减少 12 份。\n"
            "F2：首次录取 3 人，与上期持平。\n"
            "A1：数据平台岗 30 天内新增投递 0 份，计划缺口 3 人。\n"
            "【数据限制】渠道来源指标未接入。\n"
            "【可执行核对】核对数据平台岗的编制计划是否已录入。")
    assert check(text, STATS_FACTS) == []


def test_match_paraphrase_detected():
    facts = {"required_count": 2, "counts": {"met": 1, "partial": 0, "unverified": 1, "not_met": 0},
             "per_skill": [{"skill": "Go", "status": "met",
                            "evidence": {"quote": "负责订单中台网关，QPS 峰值 1.2 万", "record_no": 3}},
                           {"skill": "Kubernetes", "status": "unverified", "evidence": None}]}
    text = "核对 2 项要求：1 项有证据，0 项部分匹配，1 项待核实，0 项不满足。\nGo：技术能力扎实，经验丰富。"
    assert any(problem.startswith("evidence_not_verbatim") for problem in check(text, facts, "match"))
