from __future__ import annotations

import json
import os
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    return Path(str(files("longbridge_tax_workpaper").joinpath("data/default_jurisdiction.json")))


@lru_cache(maxsize=4)
def load_instrument_jurisdiction(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    env_value = os.environ.get("LONGBRIDGE_JURISDICTION_PATH")
    selected = Path(path or env_value or _default_path())
    if not selected.exists():
        if path is not None or env_value:
            raise FileNotFoundError(f"Instrument jurisdiction file not found: {selected}")
        raise RuntimeError(f"Packaged default jurisdiction mapping is missing: {selected}")
    data = json.loads(selected.read_text(encoding="utf-8"))
    return {str(row["security_id"]): dict(row) for row in data.get("records", [])}


def jurisdiction_for(security_id: str) -> dict[str, Any]:
    return dict(load_instrument_jurisdiction().get(security_id, {
        "security_id": security_id,
        "source_classification_status": "missing",
        "authority_level": "missing",
    }))


def clear_jurisdiction_cache() -> None:
    load_instrument_jurisdiction.cache_clear()
