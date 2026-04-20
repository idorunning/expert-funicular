"""Curated descriptions + recommended strictness for each known local model.

Rather than exposing five raw numeric thresholds to the broker we let them
pick a single 1-5 strictness level. Each level maps to a consistent bundle of
the underlying thresholds; level 3 is the calibrated default for a mid-size
model (Gemma 3 4B). The recommended level for each known model is baked in
here so switching the active model can nudge the strictness with the user's
consent.
"""
from __future__ import annotations

from dataclasses import dataclass


# -- Strictness levels -----------------------------------------------------
# Level 1 = "treat every AI guess as suspicious, flag liberally"
# Level 5 = "trust the AI, only flag the truly unknown"
#
# Each bundle sets all five underlying thresholds. Values are tuned so that
# the red/amber/green slider keeps a coherent behaviour as the user drags it.

STRICTNESS_LEVELS: dict[int, dict[str, float | int]] = {
    1: {
        "fuzzy_high": 98,
        "fuzzy_low": 60,
        "llm_confidence_threshold": 0.90,
        "confirm_weight_threshold": 4,
        "global_promote_threshold": 5,
    },
    2: {
        "fuzzy_high": 96,
        "fuzzy_low": 70,
        "llm_confidence_threshold": 0.80,
        "confirm_weight_threshold": 3,
        "global_promote_threshold": 4,
    },
    3: {
        "fuzzy_high": 92,
        "fuzzy_low": 80,
        "llm_confidence_threshold": 0.70,
        "confirm_weight_threshold": 2,
        "global_promote_threshold": 3,
    },
    4: {
        "fuzzy_high": 88,
        "fuzzy_low": 75,
        "llm_confidence_threshold": 0.60,
        "confirm_weight_threshold": 2,
        "global_promote_threshold": 3,
    },
    5: {
        "fuzzy_high": 85,
        "fuzzy_low": 70,
        "llm_confidence_threshold": 0.50,
        "confirm_weight_threshold": 1,
        "global_promote_threshold": 2,
    },
}

STRICTNESS_LABELS: dict[int, str] = {
    1: "Very cautious — flag almost everything for review.",
    2: "Cautious — flag medium matches for review.",
    3: "Balanced — recommended for most models.",
    4: "Trusting — only flag genuinely unclear merchants.",
    5: "Very trusting — accept almost every AI answer.",
}

STRICTNESS_COLORS: dict[int, str] = {
    1: "#C0392B",  # red
    2: "#E67E22",  # orange
    3: "#D4A017",  # amber
    4: "#7FB069",  # yellow-green
    5: "#27AE60",  # green
}

DEFAULT_LEVEL = 3


# -- Known model descriptions ---------------------------------------------

@dataclass(frozen=True)
class ModelInfo:
    tag: str
    display: str
    description: str
    recommended_level: int


# Map by model-tag prefix — ollama tags like "gemma3:4b" and
# "gemma3:4b-instruct-q4_0" should share a description.
_KNOWN: tuple[ModelInfo, ...] = (
    ModelInfo(
        tag="gemma4:e4b",
        display="Gemma 4 · e4b",
        description="High accuracy. Slower than Gemma 3 but more reliable on edge cases.",
        recommended_level=4,
    ),
    ModelInfo(
        tag="gemma4:4b",
        display="Gemma 4 · 4b",
        description="High accuracy. Slower than Gemma 3 but more reliable on edge cases.",
        recommended_level=4,
    ),
    ModelInfo(
        tag="gemma3:4b",
        display="Gemma 3 · 4b",
        description="Balanced. Quick and accurate on most UK bank statements.",
        recommended_level=3,
    ),
    ModelInfo(
        tag="gemma3n:e4b",
        display="Gemma 3n · e4b",
        description="Low memory. Runs on older laptops; slightly less accurate.",
        recommended_level=3,
    ),
    ModelInfo(
        tag="llama3.1:8b",
        display="Llama 3.1 · 8b",
        description="Very accurate. Needs more memory and is slower.",
        recommended_level=4,
    ),
    ModelInfo(
        tag="llama3.2:3b-instruct",
        display="Llama 3.2 · 3b",
        description="Fastest option. Quicker responses but may miss some categories.",
        recommended_level=2,
    ),
    ModelInfo(
        tag="llama3.2:3b",
        display="Llama 3.2 · 3b",
        description="Fastest option. Quicker responses but may miss some categories.",
        recommended_level=2,
    ),
    ModelInfo(
        tag="qwen2.5:7b",
        display="Qwen 2.5 · 7b",
        description="Accurate middle-ground. Slower than Gemma 3 but handles long descriptions well.",
        recommended_level=3,
    ),
)


def describe(tag: str) -> ModelInfo:
    """Return the curated info for ``tag``. Unknown tags get a safe default."""
    tag = (tag or "").strip()
    if not tag:
        return ModelInfo(
            tag="",
            display="(none selected)",
            description="Select a model to see its description.",
            recommended_level=DEFAULT_LEVEL,
        )
    for info in _KNOWN:
        if tag == info.tag or tag.startswith(info.tag + "-"):
            return info
    # Best-effort title-case for unknown tags.
    return ModelInfo(
        tag=tag,
        display=tag,
        description="Custom model. Strictness is set to Balanced; adjust the "
                    "slider if it gets things wrong or flags too much.",
        recommended_level=DEFAULT_LEVEL,
    )


def thresholds_for_level(level: int) -> dict[str, float | int]:
    level = max(1, min(5, int(level)))
    return dict(STRICTNESS_LEVELS[level])


def recommended_level_for_model(tag: str) -> int:
    return describe(tag).recommended_level
