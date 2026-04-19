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
