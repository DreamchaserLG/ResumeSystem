import json
import re
from unittest.mock import MagicMock, patch

from app.core.query_embedding import EmbeddingUnavailable
from app.core.semantic_search import _catalog_vectors, match_capabilities, role_pattern, search_condition


def test_bilingual_hr_aliases_use_word_boundaries_and_share_query_vector():
    pattern = role_pattern("人力")
    assert role_pattern("hrbp") == role_pattern("human resources") == pattern
    assert all(re.search(pattern, title) for title in ("海外hrd", "human resources manager", "高级hrbp", "集团人事"))
    assert not re.search(pattern, "thread programmer")
    with patch("app.core.semantic_search.encode_queries", side_effect=lambda values: {values[0]: "[1]"}) as encode:
        for query in ("人力", "HR", "HRBP", "Human Resources"):
            params = {}
            condition, degraded = search_condition(query, set(), params)
            assert not degraded and "resume_chunk_embedding" in condition
            assert "q.name" not in condition and "q.target_role" not in condition
            assert params["semantic_pattern"] == pattern
        assert len({tuple(call.args[0]) for call in encode.call_args_list}) == 1


def test_embedding_outage_falls_back_to_permitted_lexical_search():
    with patch("app.core.semantic_search.encode_queries", side_effect=EmbeddingUnavailable):
        condition, degraded = search_condition("HR", {("profile.name", "search")}, {})
    assert degraded and "q.name" in condition and "resume_chunk_embedding" not in condition


def test_capability_threshold_includes_boundary_and_discards_lower_scores():
    session = MagicMock()
    session.execute.side_effect = [
        MagicMock(**{"mappings.return_value.all.return_value": [
            {"code": "top_group", "name": "能力一"}, {"code": "boundary_group", "name": "能力二"},
            {"code": "below_group", "name": "能力三"},
        ]}),
        MagicMock(**{"scalars.return_value": []}),
    ]
    scores = {"能力一": .59, "能力二": .50, "能力三": .49999}
    _catalog_vectors.cache_clear()
    with patch("app.core.semantic_search.encode_queries", side_effect=lambda values, **_: {
        value: json.dumps([1 if value == "query" else scores.get(value, 0)]) for value in values
    }) as encode:
        result = match_capabilities(session, "query")
    assert result["threshold"] == .50
    assert [item["code"] for item in result["items"]] == ["top_group", "boundary_group"]
    assert encode.call_args_list[0].kwargs["lane"] == "catalog"
    assert encode.call_args_list[1].args == (["query"],)
    _catalog_vectors.cache_clear()


def test_capability_outage_uses_dictionary_without_a_vector_score():
    session = MagicMock()
    session.execute.side_effect = [
        MagicMock(**{"mappings.return_value.all.return_value": [
            {"code": "finance", "name": "财务分析"}, {"code": "sales", "name": "销售能力"},
        ]}),
        MagicMock(**{"scalars.return_value": []}),
    ]
    with patch("app.core.semantic_search.encode_queries", side_effect=EmbeddingUnavailable):
        result = match_capabilities(session, "财务")
    assert result["mode"] == "dictionary"
    assert result["items"] == [{"code": "finance", "name": "财务分析", "similarity": None}]
