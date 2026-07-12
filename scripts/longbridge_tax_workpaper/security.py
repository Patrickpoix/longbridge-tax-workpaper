"""Security ID resolution — canonical security identifiers from statement text.

Extracted from cost_basis.py to reduce module size.  All functions operate on
raw statement text and return a canonical ``MARKET:CODE`` identifier.
"""
from __future__ import annotations

import re
import unicodedata

from .symbol_mapping import resolve_symbol_alias

OPTION_CONTRACT_RE = re.compile(r"\b([A-Z]{1,6}\d{6}[CP]\d+)\b", re.IGNORECASE)
HK_CODE_RE = re.compile(r"^\s*(\d{3,5})\b")
EXPLICIT_HK_CODE_RE = re.compile(r"#?(\d{3,5})\.HK\b", re.IGNORECASE)
EXPLICIT_US_CODE_RE = re.compile(r"\b([A-Z]{1,6})(?:\.US|\s+US\s+Equity)\b", re.IGNORECASE)
US_TICKER_RE = re.compile(r"^\s*([A-Z]{1,5})\b")
SECURITY_CODE_RE = re.compile(r"#?(\d{4,5})(?:\.HK)?\b", re.IGNORECASE)


def norm_text(text: object) -> str:
    """Normalize statement text: NFKC, common replacements, collapse whitespace."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("⻩", "黄").replace("汽⻋", "汽车")
    return re.sub(r"\s+", " ", value).strip()


def compact_name(text: object) -> str:
    """Collapse text to alphanumeric characters for fuzzy matching."""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", norm_text(text)).lower()


def canonical_security_id(text: object, *, asset_type: str = "stock") -> str:
    """Resolve a symbol/name from a statement into a canonical MARKET:CODE.

    Resolution order:
    1. Option contract regex (``OPT:...``)
    2. Explicit market suffix (``.HK`` / ``.US`` / ``US Equity``)
    3. HK stock code (3-5 digits at start of token)
    4. Symbol-mapping alias table
    5. Uppercase first-token heuristic (US ticker)
    6. Fallback ``NAME:...`` or ``OPTNAME:...``
    """
    normalized = norm_text(text)
    option_match = OPTION_CONTRACT_RE.search(normalized.upper())
    if option_match:
        return f"OPT:{option_match.group(1).upper()}"

    explicit_hk = EXPLICIT_HK_CODE_RE.search(normalized)
    if explicit_hk:
        return f"HK:{int(explicit_hk.group(1)):05d}"

    explicit_us = EXPLICIT_US_CODE_RE.search(normalized.upper())
    if explicit_us:
        return f"US:{explicit_us.group(1).upper()}"

    hk_match = HK_CODE_RE.search(normalized)
    if hk_match:
        return f"HK:{int(hk_match.group(1)):05d}"

    compact = compact_name(normalized)
    mapped = resolve_symbol_alias(normalized)
    if mapped:
        return mapped

    first_token = normalized.split()[0] if normalized.split() else ""
    if re.fullmatch(r"[A-Z]{1,6}", first_token) and first_token not in {"CALL", "PUT"}:
        return f"US:{first_token}"

    prefix = "OPTNAME" if asset_type == "option" else "NAME"
    return f"{prefix}:{compact or 'unknown'}"


def security_market(security_id: str) -> str:
    """Return the market code from a canonical security ID."""
    if security_id.startswith("HK:"):
        return "HK"
    if security_id.startswith("US:") or security_id.startswith("OPT:"):
        return "US"
    return "UNKNOWN"


def asset_category(asset_type: str, symbol: str, security_id: str) -> str:
    """Classify an asset as stock, option, or warrant."""
    if asset_type == "option" or security_id.startswith("OPT:"):
        return "option"
    if security_id.startswith("HK:") and any(marker in symbol for marker in ("购A", "购B", "沽A", "沽B", "牛", "熊")):
        return "warrant"
    return "stock"
