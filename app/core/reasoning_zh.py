"""Translate model reasoning into Chinese before sending it to the browser.

Upstream SSE chunks can end in the middle of a word. Translation therefore runs
on complete sentences or bounded paragraphs, never on an arbitrary token.
"""
import re
import os
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path


ENGLISH = re.compile(r"[A-Za-z]{2,}")
BOUNDARY = re.compile(r"[.!?。！？;；\n]+(?=\s|$)|[。！？；\n]")


def local_english_to_chinese(sentence: str) -> str:
    """Run a local CTranslate2 English→Chinese model; no network request."""
    engine, tokenizer = _engine()
    tokens = tokenizer.encode(sentence, out_type=str)
    result = engine.translate_batch([tokens], beam_size=3)[0].hypotheses[0]
    return tokenizer.decode(result).replace("▁", " ").strip()


@lru_cache(maxsize=1)
def _engine():
    import ctranslate2
    import sentencepiece

    root = Path(os.environ.get("REASONING_TRANSLATION_MODEL_DIR", "/opt/reasoning-translation"))
    if not (root / "model" / "model.bin").is_file() or not (root / "sentencepiece.model").is_file():
        raise RuntimeError("缺少本地英译中模型，请运行 scripts/setup_reasoning_translation.py")
    return (ctranslate2.Translator(str(root / "model"), device="cpu", inter_threads=1),
            sentencepiece.SentencePieceProcessor(model_file=str(root / "sentencepiece.model")))


class ChineseReasoningStream:
    def __init__(self, translator: Callable[[str], str] = local_english_to_chinese,
                 *, max_pending: int = 180):
        self.translator = translator
        self.pending = ""
        self.max_pending = max_pending

    def _convert(self, fragment: str) -> str:
        if not ENGLISH.search(fragment):
            return fragment
        # The en→zh model may discard existing Chinese in mixed sentences.
        # Keep Chinese runs verbatim and only translate the surrounding English.
        parts = re.split(r"([\u3400-\u9fff]+)", fragment)
        translated = "".join(part if re.fullmatch(r"[\u3400-\u9fff]+", part)
                             or not ENGLISH.search(part) else self.translator(part)
                             for part in parts)
        # Do not silently send untranslated English if the local model is absent.
        if not translated or translated.strip() == fragment.strip():
            raise RuntimeError("离线英译中模型没有返回译文")
        return translated

    def feed(self, chunk: str) -> Iterator[str]:
        self.pending += chunk
        while self.pending:
            boundary = BOUNDARY.search(self.pending)
            if boundary:
                end = boundary.end()
            elif len(self.pending) >= self.max_pending:
                # Prefer a word boundary to avoid splitting an English word.
                split = max(self.pending.rfind(" ", 0, self.max_pending),
                            self.pending.rfind("，", 0, self.max_pending))
                end = split + 1 if split >= self.max_pending // 2 else self.max_pending
            else:
                break
            fragment, self.pending = self.pending[:end], self.pending[end:]
            yield self._convert(fragment)

    def flush(self) -> Iterator[str]:
        if self.pending:
            fragment, self.pending = self.pending, ""
            yield self._convert(fragment)
