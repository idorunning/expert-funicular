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
        "gemma3:4b",
        "gemma3n:e4b",
        "llama3.2:3b-instruct",
        "llama3.1:8b",
    )

    # Categorisation thresholds.
    fuzzy_high: int = 92
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
    "fuzzy_low": int,
    "llm_confidence_threshold": float,
    "confirm_weight_threshold": int,
    "global_promote_threshold": int,
}


def get_threshold(name: str):
    """Return the user-tuned threshold from the DB, or the env/default value.

    Falls back to ``get_settings()`` so existing tests that monkey-patch the
    Pydantic settings object keep working.
    """
    if name not in _THRESHOLD_KEYS:
        raise KeyError(f"Unknown threshold: {name!r}")
    cast = _THRESHOLD_KEYS[name]
    # Lazy import to avoid circulars (db -> config -> db).
    try:
        from .db import app_settings
    except Exception:
        return getattr(get_settings(), name)
    try:
        if cast is float:
            val = app_settings.get_float(name)
        else:
            val = app_settings.get_int(name)
    except Exception:
        val = None
    if val is not None:
        return val
    return getattr(get_settings(), name)


THRESHOLD_DEFAULTS: dict[str, float | int] = {
    "fuzzy_high": 92,
    "fuzzy_low": 80,
    "llm_confidence_threshold": 0.70,
    "confirm_weight_threshold": 2,
    "global_promote_threshold": 3,
}
