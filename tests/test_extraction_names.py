"""Names with visual spacing retain a pointer to the exact PDF heading."""
import pytest

from app.core.extraction import extract_document
from app.parsing.ir import ParsedDocument, Span


@pytest.mark.parametrize("text,source_text", [
    ("周\n宁", "周\n宁"),
    ("周 宁", "周 宁"),
    ("姓名：周 宁", "周 宁"),
])
def test_pdf_heading_name_spacing(text, source_text):
    doc = ParsedDocument(spans=[Span("heading", text, {"page": 1}, "pdf_text", reading_order=1)]).finalize()
    profile = extract_document(doc, {}, {}, {})[0]
    evidence, = profile.sources["profile.name"]
    assert profile.fields["profile.name"] == "周宁"
    assert doc.spans[0].raw_text[evidence.start:evidence.end] == source_text


def test_does_not_join_independent_headings():
    doc = ParsedDocument(spans=[Span("heading", "技能\n教育", {"page": 1}, "pdf_text", reading_order=1)]).finalize()
    assert all("profile.name" not in record.fields for record in extract_document(doc, {}, {}, {}))


def test_explicit_name_after_document_title_and_unlabeled_heading():
    doc = ParsedDocument(spans=[
        Span("heading", "演示简历", {"page":1}, "pdf_text", reading_order=1),
        Span("heading", "个人档案", {"page":1}, "pdf_text", reading_order=2),
        Span("text", "姓名：沈嘉禾", {"page":1}, "pdf_text", reading_order=3),
    ]).finalize()
    profile = extract_document(doc, {}, {}, {})[0]
    assert profile.fields["profile.name"] == "沈嘉禾"
    assert "profile.name" not in profile.issues
    assert [source.value for source in profile.sources["profile.name"]] == ["沈嘉禾"]
