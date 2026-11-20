from app.core.reasoning_zh import ChineseReasoningStream


def test_reasoning_translates_complete_sentences_from_split_sse_frames():
    translated = []
    stream = ChineseReasoningStream(lambda sentence: translated.append(sentence) or "核对岗位要求。")
    assert list(stream.feed("Review the job requi")) == []
    assert list(stream.feed("rements. 正在核对。")) == ["核对岗位要求。", " 正在核对。"]
    assert list(stream.flush()) == []
    assert translated == ["Review the job requirements."]


def test_reasoning_keeps_chinese_when_mixed_with_english():
    stream = ChineseReasoningStream(lambda sentence: "核对候选人经验。")
    assert list(stream.feed("首先 Review candidate experience. 然后继续。")) == ["首先核对候选人经验。", " 然后继续。"]
