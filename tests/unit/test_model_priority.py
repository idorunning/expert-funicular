"""Structural check on ``Settings.model_priority``.

We don't assert the exact tuple contents — just that the ordering matches
the product decision ('prefer Gemma 4, fall back to Gemma 3, then Llama').
This catches accidental regressions that would downgrade the default model
on machines where multiple tags are installed.
"""
from __future__ import annotations

from brokerledger.config import Settings


def _first_index(priority: tuple[str, ...], prefix: str) -> int:
    for i, tag in enumerate(priority):
        if tag.startswith(prefix):
            return i
    return -1


def test_default_priority_prefers_gemma4_over_gemma3():
    priority = Settings().model_priority
    g4 = _first_index(priority, "gemma4")
    g3 = _first_index(priority, "gemma3")
    assert g4 >= 0, "Gemma 4 not in priority list"
    assert g3 >= 0, "Gemma 3 not in priority list"
    assert g4 < g3, (
        "Gemma 4 must be tried before Gemma 3 so newer/bigger installs "
        f"win; got priority={priority!r}"
    )


def test_gemma_tags_come_before_llama_fallback():
    priority = Settings().model_priority
    g = min(
        i for i, tag in enumerate(priority)
        if tag.startswith("gemma3") or tag.startswith("gemma4")
    )
    llama_idx = _first_index(priority, "llama")
    assert llama_idx >= 0, "Llama fallback missing"
    assert g < llama_idx, (
        f"Gemma must be tried before Llama fallback; got priority={priority!r}"
    )


def test_priority_is_non_empty_tuple():
    priority = Settings().model_priority
    assert isinstance(priority, tuple)
    assert len(priority) >= 3
