"""Prompt loader: keeps prompt text out of code, cached per process."""
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    return (DIR / f"{name}.md").read_text(encoding="utf-8").strip()
