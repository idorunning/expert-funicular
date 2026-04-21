"""Runtime settings. Defaults can be overridden via env vars prefixed BROKERLEDGER_."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROKERLEDGER_", env_file=None)

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""  # empty = auto-detect at first run
    ollama_timeout_seconds: float = 120.0

    # Model auto-detect priority. Gemma 4 first, fall back to Gemma 3, then Llama.
    model_priority: tuple[str, ...] = (
        "gemma4:e4b",
        "gemma4:4b",
        "gemma4:e2b",
        "gemma4:2b",
        "gemma3:4b",
        "gemma3n:e4b",
        "llama3.2:3b-instruct",
        "llama3.1:8b",
    )

    # Whether to pass think=True to Ollama (enables native model chain-of-thought).
    # Models that don't support thinking ignore this flag gracefully.
    llm_native_thinking: bool = True

    # Fallback: if first LLM pass confidence < this, retry with web lookup (when enabled).
    llm_web_fallback_threshold: float = 0.45

    # Categorisation thresholds.
    fuzzy_high: int = 92
    fuzzy_medium: int = 85
    fuzzy_low: int = 80
    llm_confidence_threshold: float = 0.70
    confirm_weight_threshold: int = 2
    global_promote_threshold: int = 3

    # Auth.
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    # Categorise-loop limits.
    few_shot_k: int = 12
    llm_max_retries: int = 1

    # Debug/testing.
    fake_llm: bool = Field(default=False, description="Bypass Ollama; use a deterministic stub")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None


# Keys the user can override live in the Settings UI. Values are stored in the
# AppSetting key/value table and read on every categorisation to keep the
# feedback loop tight — these five reads per transaction are cheap.
_THRESHOLD_KEYS: dict[str, type] = {
    "fuzzy_high": int,
    "fuzzy_medium": int,
    "fuzzy_low": int,
    "llm_confidence_threshold": float,
    "confirm_weight_threshold": int,
    "global_promote_threshold": int,
}


STRICTNESS_KEY = "category_strictness"
DEFAULT_STRICTNESS_LEVEL = 3


def get_strictness_level() -> int:
    """Active strictness level (1-5). Falls back to the default if unset."""
    try:
        from .db import app_settings
    except Exception:
        return DEFAULT_STRICTNESS_LEVEL
    try:
        val = app_settings.get_int(STRICTNESS_KEY)
    except Exception:
        val = None
    if val is None:
        return DEFAULT_STRICTNESS_LEVEL
    return max(1, min(5, int(val)))


def set_strictness_level(level: int) -> None:
    from .db import app_settings
    level = max(1, min(5, int(level)))
    app_settings.put(STRICTNESS_KEY, str(level))


def get_threshold(name: str):
    """Return the threshold for the active strictness level.

    Individual env-var / pydantic-settings overrides still win (tests that
    monkey-patch ``Settings`` keep working), then any explicit per-key
    override stored in ``app_settings`` (legacy), then the strictness bundle.
    """
    if name not in _THRESHOLD_KEYS:
        raise KeyError(f"Unknown threshold: {name!r}")
    cast = _THRESHOLD_KEYS[name]

    # 1. Env / Pydantic override — tests rely on this path.
    env_val = getattr(get_settings(), name)
    defaults = THRESHOLD_DEFAULTS[name]
    if env_val != defaults:
        return env_val

    # 2. Per-key override stored in app_settings (legacy).
    try:
        from .db import app_settings
    except Exception:
        return env_val
    try:
        if cast is float:
            val = app_settings.get_float(name)
        else:
            val = app_settings.get_int(name)
    except Exception:
        val = None
    if val is not None:
        return val

    # 3. Bundle from the active strictness level.
    from .categorize.model_catalog import thresholds_for_level
    bundle = thresholds_for_level(get_strictness_level())
    return bundle[name]


THRESHOLD_DEFAULTS: dict[str, float | int] = {
    "fuzzy_high": 92,
    "fuzzy_medium": 85,
    "fuzzy_low": 80,
    "llm_confidence_threshold": 0.70,
    "confirm_weight_threshold": 2,
    "global_promote_threshold": 3,
}
