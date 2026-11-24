"""Regression tests for classifying job references and slicing source ranges."""
from app.core.job_match_score import evidence_score, semantic_evidence, skill_pattern, verified_mention
from app.core.vector_index import MAX_TOKENS, OVERLAP_TOKENS, job_requirements, make_chunks
from app.core.vector_retrieval import fuse, rank_lane


def test_reference_keeps_source_items_separate_and_qualifications_unconfirmed():
    source, requirements = job_requirements({
        "title": "演示医师",
        "jd": "参与门诊；管理住院患者。",
        "requirements": "掌握病历书写。\n执业注册范围由医院核验。",
        "required_skills": ["Go 或 Java", "加分项：Docker"],
        "reference": {
            "scopes": ["work"],
            "keywords": {"参与门诊;管理住院患者": ["门诊", "住院"]},
        },
    }, {})
    assert source == "reference"
    assert [item.kind for item in requirements] == [
        "duty", "qualification", "qualification_check", "skill", "skill",
    ]
    assert requirements[0].terms == ("门诊", "住院")
    assert requirements[0].scopes == requirements[1].scopes == ("work",)
    assert requirements[2].query is None and requirements[2].minimum is None
    assert (requirements[3].terms, requirements[3].weight) == (("go", "java"), 2)
    assert (requirements[4].terms, requirements[4].weight) == (("docker",), 1)


class CharacterTokenizer:
    def __call__(self, value, *, add_special_tokens=True, return_offsets_mapping=False, **_):
        if return_offsets_mapping:
            return {"offset_mapping": [(index, index + 1) for index in range(len(value))]}
        return {"input_ids": range(len(value) + (2 if add_special_tokens else 0))}


def test_long_work_source_keeps_tail_and_only_forced_windows_overlap():
    span = "职责" * 620
    rows = [{
        "kind": "work", "employment_type": "internship", "part_no": 1, "span_no": 8,
        "field_key": "work.description", "span_kind": "text", "start_offset": 10, "value": span,
    }, {
        "kind": "work", "employment_type": "internship", "part_no": 2, "span_no": 9,
        "field_key": "work.description", "span_kind": "text", "start_offset": 5, "value": "后续成果",
    }]
    chunks = list(make_chunks(CharacterTokenizer(), rows))
    windows = [part for chunk in chunks for part in chunk.fragments if part.span_no == 8]
    assert all(chunk.token_count <= MAX_TOKENS and chunk.kind == "internship" for chunk in chunks)
    assert windows[0].start == 10
    assert windows[-1].start + len(windows[-1].value) == 10 + len(span)
    assert all(a.start + len(a.value) - b.start == OVERLAP_TOKENS for a, b in zip(windows, windows[1:]))
    assert sum(part.span_no == 9 for chunk in chunks for part in chunk.fragments) == 1


def test_rrf_uses_ranks_and_preserves_ties_between_equal_evidence():
    vector = rank_lane({(1, 1): {"relevance": 0.8}, (2, 1): {"relevance": 0.8},
                        (3, 1): {"relevance": 0.7}}, {1: 1})
    keywords = rank_lane({(2, 1): {"relevance": 1}}, {1: 1})
    assert vector[1] == vector[2]
    assert fuse([vector, keywords]) == [2, 1, 3]



def test_evidence_formula_matches_review_example_and_keeps_missing_weight():
    requirements = [
        {"kind": "duty", "weight": 3, "requirement_no": 1},
        {"kind": "duty", "weight": 2, "requirement_no": 2},
        {"kind": "skill", "weight": 2, "requirement_no": 3, "mandatory": True, "original_text": "Go"},
        {"kind": "skill", "weight": 2, "requirement_no": 4, "mandatory": True, "original_text": "Kafka"},
        {"kind": "skill", "weight": 1, "requirement_no": 5, "mandatory": False, "original_text": "Docker"},
        {"kind": "degree", "weight": 1, "requirement_no": 6},
    ]
    result = evidence_score(requirements, {1: 0.8, 2: 0.6, 6: 1.0}, {3: 1, 5: 1})
    assert (result["total"], result["duty"], result["skill"]) == (67.8, 72.0, 60.0)
    assert result["missing_required_skills"] == ["Kafka"]
    assert evidence_score(requirements, {1: 0.8}, {})["duty"] == 48.0
    assert semantic_evidence(-0.2) == semantic_evidence(None) == semantic_evidence(float("nan")) == 0
    assert semantic_evidence(1.01) == 1


def test_lexical_skill_requires_an_affirmative_term():
    pattern = skill_pattern(["Go", "Golang", "商业招商"])
    assert verified_mention(pattern, "使用 Go 开发") == "go"
    assert verified_mention(pattern, "负责商业招商") == "商业招商"
    assert verified_mention(pattern, "不会 Go，熟悉 Java") is None
    assert verified_mention(pattern, "无商业招商经验") is None
    assert verified_mention(pattern, "熟悉 Django") is None
