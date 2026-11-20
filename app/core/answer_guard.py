"""Reply guard: turn prompt-level bans into deterministic checks."""
import json
import re

BANNED = (
    "整体表现良好", "情况稳定", "较为理想", "值得关注", "建议重点关注", "提升空间",
    "总体健康", "成效显著", "明显改善", "有待加强", "需要重视", "综合来看",
    "沟通能力强", "技术扎实", "综合素质", "匹配度高", "非常合适", "值得培养",
    "建议录用", "建议淘汰", "建议安排", "从数据上看",
)
NUM = re.compile(r"\d+(?:\.\d+)?")
FID = re.compile(r"([FA]\d+)：")
SCORE = re.compile(r"\d+\s*(?:分|%|％)|[ABC]\s*档|排名\s*\d")
# 保留有证据的展开分析，同时防止失控的长回答。
LIMIT = {"stats": 900, "match": 750}


def _walk(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
    elif isinstance(node, str):
        yield node


def fact_numbers(facts) -> set[str]:
    """Every numeric token that legitimately appears in the fact block."""
    facts = {key: value for key, value in facts.items() if key != "question"}
    allowed: set[str] = set()
    for chunk in _walk(facts):
        allowed.update(NUM.findall(chunk))
    allowed.update(NUM.findall(json.dumps(facts, ensure_ascii=False)))
    return allowed


def fact_ids(facts) -> set[str]:
    ids = {row["id"] for row in facts.get("facts", [])}
    ids |= {row["id"] for row in facts.get("anomalies", [])}
    return ids


def _overlap(a: str, b: str, n: int = 6) -> int:
    """最长公共子串是否达到 n（只判断存在性，够用且 O(len)）。"""
    if len(a) < n or len(b) < n:
        return 0
    grams = {a[i:i + n] for i in range(len(a) - n + 1)}
    for i in range(len(b) - n + 1):
        if b[i:i + n] in grams:
            return n
    return 0


def _trace_problems(text: str, facts: dict, kind: str) -> list[str]:
    """stats 靠 fact_id 追溯；match 靠 evidence 原文逐字追溯（两类格式不同，不能共用一套）。"""
    if kind == "match":
        quotes = {row["skill"]:row["evidence"]["quote"] for row in facts.get("per_skill", [])
                  if row.get("evidence")}
        region = text
        if "【原文证据】" in text:
            region = text.split("【原文证据】",1)[1].split("【待核实】",1)[0].split("【人工下一步】",1)[0]
        evidence = [(line.strip(),skill,quote) for line in region.splitlines()
                    for skill,quote in quotes.items()
                    if re.match(r"^\s*[-•]?\s*" + re.escape(skill) + r"：", line)]
        bad = [line for line,_,quote in evidence if _overlap(line, quote) < 6]
        if quotes and not evidence:
            return ["evidence_missing"]
        return ["evidence_not_verbatim:" + str(len(bad))] if bad else []
    used = FID.findall(text)
    if not used:
        return ["no_fact_id"]
    if set(used) - fact_ids(facts):
        return ["unknown_fact_id:" + ",".join(sorted(set(used) - fact_ids(facts)))]
    if len(used) != len(set(used)):
        return ["duplicate_fact_id"]
    return []


def check(text: str, facts: dict, kind: str = "stats") -> list[str]:
    text = (text or "").strip()
    problems: list[str] = []
    if not text:
        return ["empty"]

    hits = [word for word in BANNED if word in text]
    if hits:
        problems.append("banned_phrase:" + ",".join(hits))

    # fact_id 前缀里的序号不是数据，先剥离再抽数字，否则 F9 会被判成越界数字
    numbers = set(NUM.findall(FID.sub("", text))) - fact_numbers(facts)
    if numbers:
        problems.append("number_out_of_scope:" + ",".join(sorted(numbers)))

    problems += _trace_problems(text, facts, kind)

    if len(text) > LIMIT.get(kind, 220):
        problems.append(f"too_long:{len(text)}")
    if kind == "match":
        counts = facts.get("counts", {})
        expected = (f"核对 {facts.get('required_count')} 项要求："
                    f"{counts.get('met')} 项有证据，{counts.get('partial')} 项部分匹配，"
                    f"{counts.get('unverified')} 项待核实，{counts.get('not_met')} 项不满足。")
        if expected not in text:
            problems.append("count_mismatch")
        if SCORE.search(text):
            problems.append("score_or_grade")
    return problems
