from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

PENDING_REVIEW_CATEGORIES = {
    "service_fee_pending_review",
    "company_action_fee_pending_review",
    "company_action_cash_pending_review",
    "cash_reward_pending_review",
    "pending_review",
}


def _resource_path(name: str) -> Path:
    return Path(str(files("longbridge_tax_workpaper").joinpath(f"data/{name}")))


def _normalize_rate(value: object, *, currency: str, source: Path) -> float | None:
    if value in (None, ""):
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid year-end FX rate for {currency} in {source}: {value!r}") from exc
    if rate <= 0:
        raise ValueError(f"Year-end FX rate for {currency} must be positive in {source}: {value!r}")
    return float(rate)


def _select_path(explicit: str | Path | None, env_name: str, default_name: str) -> tuple[Path, bool]:
    env_value = os.environ.get(env_name)
    if explicit is not None:
        return Path(explicit), True
    if env_value:
        return Path(env_value), True
    return _resource_path(default_name), False


@lru_cache(maxsize=8)
def load_tax_policy(path: str | Path | None = None) -> dict[str, Any]:
    selected, explicitly_selected = _select_path(path, "LONGBRIDGE_TAX_POLICY_PATH", "default_tax_policy.json")
    if not selected.exists():
        if explicitly_selected:
            raise FileNotFoundError(f"Tax policy file not found: {selected}")
        raise RuntimeError(f"Packaged default tax policy is missing: {selected}")
    data = json.loads(selected.read_text(encoding="utf-8"))
    rates = data.setdefault("year_end_fx_rates", {})
    for currency in ("HKD", "USD"):
        item = rates.setdefault(currency, {})
        item["rate"] = _normalize_rate(item.get("rate"), currency=currency, source=selected)
        item.setdefault("source_status", "missing" if item["rate"] is None else "provided")
    data["_source_path"] = str(selected)
    return data


def year_end_fx_rate(currency: str, policy: dict[str, Any] | None = None) -> float | None:
    policy = policy or load_tax_policy()
    value = policy.get("year_end_fx_rates", {}).get(currency, {}).get("rate")
    return None if value in (None, "") else float(value)


def require_year_end_fx_rate(currency: str, policy: dict[str, Any] | None = None) -> float:
    rate = year_end_fx_rate(currency, policy)
    if rate is None:
        raise ValueError(f"Missing year-end FX rate for {currency}")
    return rate


def missing_year_end_fx_currencies(policy: dict[str, Any] | None = None) -> list[str]:
    policy = policy or load_tax_policy()
    return [currency for currency in ("USD", "HKD") if year_end_fx_rate(currency, policy) is None]


def category_rule(category: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_tax_policy()
    return dict(policy.get("category_rules", {}).get(category, {}))


def is_pending_review_category(category: str) -> bool:
    return category in PENDING_REVIEW_CATEGORIES or category.endswith("_pending_review")


@lru_cache(maxsize=8)
def load_taxpayer_profile(path: str | Path | None = None) -> dict[str, Any]:
    selected, explicitly_selected = _select_path(path, "LONGBRIDGE_TAXPAYER_PROFILE_PATH", "default_taxpayer_profile.json")
    if not selected.exists():
        if explicitly_selected:
            raise FileNotFoundError(f"Taxpayer profile file not found: {selected}")
        raise RuntimeError(f"Packaged default taxpayer profile is missing: {selected}")
    data = json.loads(selected.read_text(encoding="utf-8"))
    data["_source_path"] = str(selected)
    return data


def clear_policy_caches() -> None:
    load_tax_policy.cache_clear()
    load_taxpayer_profile.cache_clear()
