"""Transparent score components for the reviewed evidence formula."""
import math
import re

from app.core.extraction import norm

DUTY_WEIGHT = 0.65
SKILL_WEIGHT = 0.35
DUTY_KINDS = ("duty", "role")
SCORE_BASIS = "uncalibrated_positive_cosine"
CLAUSE_BOUNDARY = re.compile(r"[。！？；;，,\n]")
NEGATION = re.compile(r"(?:未(?:曾|能|有)?|没有|不(?:会|具备|熟悉|掌握|擅长)|无)"
                      r"[^。！？；;，,\n]{0,8}$|\b(?:no|not|without|never)\s+(?:\w+\s+){0,3}$", re.I)


def semantic_evidence(cosine):
    if cosine is None or not math.isfinite(cosine):
        return 0.0
    return min(1.0, max(0.0, cosine))


def evidence_score(requirements, duty_evidence, skill_evidence):
    def weighted(kinds, evidence):
        relevant = [item for item in requirements if item["kind"] in kinds]
        denominator = sum(item["weight"] for item in relevant)
        if not denominator:
            return 0.0
        return sum(item["weight"] * evidence.get(item["requirement_no"], 0.0)
                   for item in relevant) / denominator

    duty = weighted(DUTY_KINDS, duty_evidence)
    skill = weighted(("skill",), skill_evidence)
    required = [item for item in requirements if item["kind"] == "skill" and item["mandatory"]]
    return {
        "total": round(100 * (DUTY_WEIGHT * duty + SKILL_WEIGHT * skill), 2),
        "duty": round(100 * duty, 2),
        "skill": round(100 * skill, 2),
        "basis": SCORE_BASIS,
        "duty_weight": DUTY_WEIGHT,
        "skill_weight": SKILL_WEIGHT,
        "missing_required_skills": [item["original_text"] for item in required
                                    if not skill_evidence.get(item["requirement_no"])],
    }


def skill_pattern(terms):
    variants = []
    for term in sorted({norm(term) for term in terms if norm(term)}, key=lambda value: (-len(value), value)):
        phrase = re.escape(term)
        if term[0].isascii() and term[0].isalnum():
            phrase = r"(?<![a-z0-9_])" + phrase
        if term[-1].isascii() and (term[-1].isalnum() or term[-1] in "+#"):
            phrase += r"(?![a-z0-9_+#])"
        variants.append(phrase)
    return re.compile("|".join(variants)) if variants else None


def verified_mention(pattern, document):
    if pattern is None:
        return None
    # Keep clause boundaries for negation checks before normalizing individual clauses.
    for clause in CLAUSE_BOUNDARY.split(document):
        line = norm(clause)
        for hit in pattern.finditer(line):
            before, after = line[:hit.start()].rstrip("：: "), line[hit.end():].lstrip()
            if not NEGATION.search(before) and not re.match(r"(?:经验)?\s*(?:暂无|缺失|待学习)|[:：]\s*无", after):
                return hit.group()
    return None
