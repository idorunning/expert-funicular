"""Opt-in merchant web lookup.

OFF by default. When the admin ticks *Enable merchant web lookup* in
Settings, the categoriser may send **only the normalised merchant name**
to the public DuckDuckGo Instant Answer API before calling the local LLM.
The returned abstract (a short text snippet) is injected into the LLM
prompt to improve categorisation of obscure merchants.

Nothing else ever leaves the machine. Specifically we do not send:
- the customer name, account number, or IBAN
- the transaction amount, date, direction, or balance
- the description line (which may contain payee names, addresses,
  reference numbers, etc.)

The merchant string has already been normalised and has names / account
numbers / postcodes stripped — see :mod:`brokerledger.ingest.normalize`.

If this module is disabled (the default) all its public entry points are
no-ops returning ``None``, and no outbound network request is made.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

from ..db import app_settings
from ..utils.logging import logger


SETTING_KEY = "llm_web_search_enabled"
ENDPOINT = "https://api.duckduckgo.com/"
_TIMEOUT_SECONDS = 3.0
_MAX_ABSTRACT_CHARS = 280


def is_enabled() -> bool:
    """True if the admin has explicitly opted in to web lookup."""
    return app_settings.get_bool(SETTING_KEY, default=False)


def set_enabled(value: bool) -> None:
    app_settings.put(SETTING_KEY, "1" if value else "0")


def _sanitise_merchant(merchant: str) -> str:
    """Keep only letters/digits/spaces; trim to a reasonable length.

    Defence in depth — the normalised merchant should already be clean,
    but this guarantees we never accidentally send digits that could be
    an account number or reference.
    """
    cleaned = re.sub(r"[^A-Za-z\s]", " ", merchant or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:60]


def lookup_merchant(merchant: str) -> str | None:
    """Return a short plain-text description of ``merchant`` or ``None``.

    Safe to call unconditionally — if the feature is disabled, or the
    merchant string is empty after sanitisation, or the network request
    fails, this returns ``None`` and the caller carries on as normal.
    """
    if not is_enabled():
        return None
    query = _sanitise_merchant(merchant)
    if not query or len(query) < 3:
        return None
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    url = f"{ENDPOINT}?{urlencode(params)}"
    try:
        r = httpx.get(url, timeout=_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("Web lookup failed for {}: {}", query, e)
        return None
    abstract = (data.get("AbstractText") or data.get("Heading") or "").strip()
    if not abstract:
        logger.info("Web lookup for {!r} returned no abstract", query)
        return None
    if len(abstract) > _MAX_ABSTRACT_CHARS:
        abstract = abstract[: _MAX_ABSTRACT_CHARS - 1].rstrip() + "…"
    logger.info("Web lookup for {!r}: {:.80s}", query, abstract)
    return abstract
