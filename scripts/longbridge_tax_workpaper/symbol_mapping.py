from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any


def normalize_alias(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).lower()
    return value


def _default_path() -> Path:
    return Path(str(files("longbridge_tax_workpaper").joinpath("data/default_symbol_mapping.json")))


@lru_cache(maxsize=4)
def load_symbol_mapping(path: str | Path | None = None) -> dict[str, Any]:
    selected = Path(path or os.environ.get("LONGBRIDGE_SYMBOL_MAPPING_PATH") or _default_path())
    data = json.loads(selected.read_text(encoding="utf-8"))
    alias_index: dict[str, str] = {}
    duplicate_aliases: list[str] = []
    for record in data.get("records", []):
        security_id = str(record.get("security_id") or "").strip()
        if not security_id:
            continue
        for alias in record.get("aliases", []):
            normalized = normalize_alias(alias)
            if not normalized:
                continue
            existing = alias_index.get(normalized)
            if existing and existing != security_id:
                duplicate_aliases.append(normalized)
                continue
            alias_index[normalized] = security_id
    if duplicate_aliases:
        raise ValueError(f"symbol mapping contains conflicting aliases: {sorted(set(duplicate_aliases))}")
    data["_source_path"] = str(selected)
    data["_alias_index"] = alias_index
    return data


def resolve_symbol_alias(text: object, path: str | Path | None = None) -> str | None:
    normalized = normalize_alias(text)
    if not normalized:
        return None
    return load_symbol_mapping(path).get("_alias_index", {}).get(normalized)


def clear_symbol_mapping_cache() -> None:
    load_symbol_mapping.cache_clear()
