"""LLM client interface + Ollama HTTP implementation."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..utils.logging import logger
from .prompts import FewShotExample, build_system_prompt, build_user_prompt
from .taxonomy import category_names, group_of


@dataclass
class LLMResult:
    category: str
    group: str
    confidence: float
    reason: str


class LLMError(Exception):
    pass


class LLMClient(ABC):
    @abstractmethod
    def classify(
        self,
        description_raw: str,
        merchant_normalized: str,
        amount: Decimal,
        direction: str,
        posted_date: str,
        few_shot: list[FewShotExample],
    ) -> LLMResult: ...


def _assert_local(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise LLMError(f"Refusing non-local LLM endpoint: {url!r}")


class OllamaClient(LLMClient):
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float | None = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ollama_url).rstrip("/")
        _assert_local(self.base_url)
        self.model = model or s.ollama_model
        self.timeout = timeout if timeout is not None else s.ollama_timeout_seconds
        if not self.model:
            self.model = self._auto_pick_model(s.model_priority)

    def _auto_pick_model(self, priority: tuple[str, ...]) -> str:
        try:
            available = self.list_models()
        except LLMError:
            # If we can't reach Ollama, fall back to the first priority; classify will error clearly later.
            return priority[0]
        avail_set = {m for m in available}
        for m in priority:
            if m in avail_set:
                return m
        if available:
            return available[0]
        return priority[0]

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Could not reach Ollama at {self.base_url}: {e}") from e
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def classify(
        self,
        description_raw: str,
        merchant_normalized: str,
        amount: Decimal,
        direction: str,
        posted_date: str,
        few_shot: list[FewShotExample],
    ) -> LLMResult:
        system = build_system_prompt()
        user = build_user_prompt(description_raw, merchant_normalized, amount, direction, posted_date, few_shot)

        body = {
            "model": self.model,
            "prompt": f"{system}\n\n{user}",
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        }
        try:
            r = httpx.post(f"{self.base_url}/api/generate", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            raise LLMError(f"LLM call failed: {e}") from e

        text = data.get("response", "").strip()
        return _parse_llm_json(text)


def _parse_llm_json(text: str) -> LLMResult:
    if not text:
        raise LLMError("Empty LLM response")
    # Tolerate stray prose around the JSON by picking the first {...} block.
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise LLMError(f"LLM did not return JSON: {text[:200]!r}")
        obj = json.loads(text[start : end + 1])

    cat = str(obj.get("category", "")).strip()
    if cat not in category_names():
        raise LLMError(f"LLM returned invalid category: {cat!r}")
    grp = str(obj.get("group", "")).strip()
    expected_group = group_of(cat)
    if grp != expected_group:
        grp = expected_group  # self-heal
    conf = obj.get("confidence", 0.0)
    try:
        conf_f = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf_f = 0.0
    reason = str(obj.get("reason", ""))[:200]
    return LLMResult(category=cat, group=grp, confidence=conf_f, reason=reason)


class FakeLLMClient(LLMClient):
    """Deterministic stub used in tests and when ollama is unavailable.

    Picks the first taxonomy category by a simple keyword mapping, with a
    modest confidence. The real LLM is never called.
    """

    _KEYWORD_MAP: dict[str, str] = {
        "RENT": "Other mortgage / Rent",
        "COUNCIL TAX": "Council tax",
        "GAS": "Electricity / Gas / Oil",
        "ELECTRIC": "Electricity / Gas / Oil",
        "ENERGY": "Electricity / Gas / Oil",
        "WATER": "Water",
        "SKY": "Television",
        "NETFLIX": "Entertainment",
        "SPOTIFY": "Entertainment",
        "BT ": "Communications",
        "VODAFONE": "Communications",
        "TESCO": "Food",
        "SAINSBURYS": "Food",
        "ASDA": "Food",
        "MORRISONS": "Food",
        "UBER": "Other transport costs",
        "TFL": "Other transport costs",
        "TRAINLINE": "Other transport costs",
        "BP ": "Car costs",
        "SHELL": "Car costs",
        "AVIVA": "Insurances",
        "NEST": "Pension contributions",
        "VANGUARD": "Investments",
        "NURSERY": "Child care",
        "CHILDCARE": "Child care",
        "HOLIDAY": "Holidays",
        "EASYJET": "Holidays",
        "SALARY": "Salary/Wages",
        "WAGE": "Salary/Wages",
    }

    def classify(
        self,
        description_raw: str,
        merchant_normalized: str,
        amount: Decimal,
        direction: str,
        posted_date: str,
        few_shot: list[FewShotExample],
    ) -> LLMResult:
        # Few-shot wins deterministically.
        for ex in few_shot:
            if ex.merchant and ex.merchant in merchant_normalized:
                return LLMResult(ex.category, group_of(ex.category), 0.85, "few-shot hint")
        for key, cat in self._KEYWORD_MAP.items():
            if key in merchant_normalized:
                return LLMResult(cat, group_of(cat), 0.7, f"keyword match {key!r}")
        if direction == "credit":
            return LLMResult("Other income", "income", 0.4, "credit default")
        return LLMResult("Food", "discretionary", 0.2, "low-confidence default")


def get_llm_client() -> LLMClient:
    s = get_settings()
    if s.fake_llm:
        logger.info("FAKE LLM mode enabled")
        return FakeLLMClient()
    try:
        return OllamaClient()
    except LLMError as e:
        logger.warning("Falling back to FakeLLMClient: {}", e)
        return FakeLLMClient()
