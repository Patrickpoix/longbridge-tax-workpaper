from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator, Mapping

from .filing_policy import clear_policy_caches
from .jurisdiction import clear_jurisdiction_cache
from .symbol_mapping import clear_symbol_mapping_cache

_RUNTIME_ENV = {
    "policy": "LONGBRIDGE_TAX_POLICY_PATH",
    "profile": "LONGBRIDGE_TAXPAYER_PROFILE_PATH",
    "jurisdiction": "LONGBRIDGE_JURISDICTION_PATH",
    "symbol_mapping": "LONGBRIDGE_SYMBOL_MAPPING_PATH",
}


def _validated_rate(value: object) -> str | None:
    """Validate an FX rate and return its exact string representation."""
    if value in (None, ""):
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid FX rate: {value!r}") from exc
    if rate <= 0:
        raise ValueError(f"FX rate must be positive: {value!r}")
    return str(rate)


def _default_policy(
    tax_year: int,
    fx_rates: dict[str, str | Decimal] | None = None,
    fx_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fx_rates = fx_rates or {}
    fx_metadata = fx_metadata or {}

    def fx_entry(currency: str) -> dict[str, Any]:
        rate = _validated_rate(fx_rates.get(currency))
        metadata = fx_metadata.get(currency, {})
        return {
            "rate": rate,
            "unit": f"1 {currency} = ? CNY" if rate is None else f"1 {currency} = {rate} CNY",
            "source_status": "missing" if rate is None else str(metadata.get("source_status") or "user_or_model_supplied"),
            "source_date": str(metadata.get("source_date") or f"{tax_year}-12-31"),
            "source_url": metadata.get("source_url"),
            "evidence_sha256": metadata.get("evidence_sha256"),
        }

    return {
        "policy_version": f"{tax_year}-CN-longbridge-workpaper-v4",
        "tax_year": tax_year,
        "reporting_currency": "CNY",
        "year_end_date": f"{tax_year}-12-31",
        "year_end_fx_rates": {"USD": fx_entry("USD"), "HKD": fx_entry("HKD")},
        "category_rules": {
            "service_fee_deductible": {
                "label": "股息相关券商处理费",
                "treatment": "shown_separately_not_deducted_by_default",
                "deductible_in_final_filing": False,
            },
            "company_action_fee_non_deductible": {
                "label": "公司行动其他费用",
                "treatment": "shown_separately",
                "deductible_in_final_filing": False,
            },
            "cash_reward_other_income": {
                "label": "现金奖励（其他/偶然性质收入候选）",
                "classification_status": "conservative_candidate",
                "tax_rate": 0.20,
            },
            "margin_interest_deductible": {
                "label": "融资利息",
                "treatment": "non_deductible_default",
                "deductible_in_final_filing": False,
            },
        },
        "property_transfer_loss_offset": {
            "status": "scenario_only",
            "treatment": "show_multiple_scenarios",
            "include_unrealized_pnl": False,
            "tax_rate": 0.20,
            "authority_status": "not_embedded_as_final_law",
            "note": "输出分市场、同账户跨市场和不抵亏三种测算情景；不自动认定唯一税务口径。",
        },
        "dividend_filing_basis": {
            "tax_rate": 0.20,
            "automatic_credit_without_formal_documents": 0.0,
            "statement_withholding_as_candidate": True,
            "note": "月结单扣税列为抵免候选；自动抵免默认为0。",
        },
        "precision": {
            "internal_decimal_places": 8,
            "cny_output_decimal_places": 2,
            "rounding": "ROUND_HALF_UP",
            "annual_rounding_order": "sum_unrounded_rows_then_round_output",
        },
    }


def _default_profile(tax_year: int, account_opening_month: str | None) -> dict[str, Any]:
    next_year = tax_year + 1
    return {
        "profile_version": f"{tax_year}-CN-single-longbridge-account-v4",
        "tax_year": tax_year,
        "tax_residency": {
            "jurisdiction": "CN",
            "is_china_tax_resident": True,
            "basis": "tool_scope_default",
            "note": "本工具默认面向中国内地税收居民。",
        },
        "competent_tax_authority_location": {
            "province_or_municipality": None,
            "city": None,
            "district": None,
            "note": "可选填写；不影响工作底稿生成。",
        },
        "employment": {"has_employer": None, "status": "未填写"},
        "foreign_income_filing": {
            f"filed_for_{tax_year}": None,
            "status": "未填写",
            "ordinary_deadline_start": f"{next_year}-03-01",
            "ordinary_deadline_end": f"{next_year}-06-30",
            "overdue": None,
        },
        "scope_confirmation": {
            "single_longbridge_account_only": True,
            "other_foreign_broker_accounts": None,
            "other_foreign_income": None,
        },
        "foreign_tax_credit_documents": {
            "available": False,
            "documents": [],
            "treatment": "automatic_credit_zero_statement_candidate_only",
        },
        "cost_basis_method": {
            "selected_method": None,
            "preferred_method": "MOVING_AVERAGE",
            "methods_produced": ["FIFO", "MOVING_AVERAGE"],
            "status": "method_unconfirmed",
        },
        "account_opening_month": account_opening_month,
        "attestation": {
            "record_type": "generated_template",
            "confirmed_at": date.today().isoformat(),
            "note": "用户事实可在工作簿中补充；系统不会把税务口径伪装成用户事实。",
        },
    }


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _default_symbol_mapping_path() -> Path:
    return Path(str(files("longbridge_tax_workpaper").joinpath("data/default_symbol_mapping.json")))


def prepare_runtime_config(
    config_dir: str | Path,
    *,
    tax_year: int,
    account_opening_month: str | None,
    fx_rates: dict[str, str | Decimal] | None = None,
    fx_metadata: dict[str, dict[str, Any]] | None = None,
    policy_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    jurisdiction_path: str | Path | None = None,
    symbol_mapping_path: str | Path | None = None,
    cost_basis_method: str = "BOTH",
    withholding_credit: bool = False,
    deduct_margin_interest: bool = False,
) -> dict[str, Path]:
    """Materialize an auditable runtime configuration without mutating globals."""

    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    policy_target = config_dir / "tax_policy.json"
    profile_target = config_dir / "taxpayer_profile.json"
    jurisdiction_target = config_dir / "instrument_jurisdiction.json"
    symbol_target = config_dir / "symbol_mapping.json"

    if policy_path:
        policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        policy["tax_year"] = tax_year
        policy["year_end_date"] = f"{tax_year}-12-31"
        if fx_rates:
            for currency, raw_rate in fx_rates.items():
                rate = _validated_rate(raw_rate)
                item = policy.setdefault("year_end_fx_rates", {}).setdefault(currency, {})
                item["rate"] = rate
                metadata = (fx_metadata or {}).get(currency, {})
                item["source_status"] = str(metadata.get("source_status") or "user_or_model_supplied")
                item["source_date"] = str(metadata.get("source_date") or f"{tax_year}-12-31")
                if "source_url" in metadata:
                    item["source_url"] = metadata.get("source_url")
                if "evidence_sha256" in metadata:
                    item["evidence_sha256"] = metadata.get("evidence_sha256")
    else:
        policy = _default_policy(tax_year, fx_rates, fx_metadata)
    # Apply user-selected tax treatment options to policy
    if deduct_margin_interest:
        policy.setdefault("category_rules", {}).setdefault("margin_interest_deductible", {})["deductible_in_final_filing"] = True
        policy["category_rules"]["margin_interest_deductible"]["treatment"] = "deductible"
    if withholding_credit:
        policy.setdefault("dividend_filing_basis", {})["automatic_credit_without_formal_documents"] = 1.0
    _write_json(policy_target, policy)

    if profile_path:
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        profile["tax_year"] = tax_year
        if not profile.get("account_opening_month"):
            profile["account_opening_month"] = account_opening_month
    else:
        profile = _default_profile(tax_year, account_opening_month)
    # Apply user-selected cost basis method to profile
    methods_produced = {"FIFO": ["FIFO"], "MOVING_AVERAGE": ["MOVING_AVERAGE"], "BOTH": ["FIFO", "MOVING_AVERAGE"]}
    profile.setdefault("cost_basis_method", {})["methods_produced"] = methods_produced.get(cost_basis_method, ["FIFO", "MOVING_AVERAGE"])
    if cost_basis_method != "BOTH":
        profile["cost_basis_method"]["selected_method"] = cost_basis_method
        profile["cost_basis_method"]["status"] = "user_selected"
    _write_json(profile_target, profile)

    if jurisdiction_path:
        jurisdiction = json.loads(Path(jurisdiction_path).read_text(encoding="utf-8"))
    else:
        jurisdiction = {"mapping_version": f"{tax_year}-unresolved-v4", "records": []}
    _write_json(jurisdiction_target, jurisdiction)

    shutil.copy2(Path(symbol_mapping_path) if symbol_mapping_path else _default_symbol_mapping_path(), symbol_target)
    return {
        "policy": policy_target,
        "profile": profile_target,
        "jurisdiction": jurisdiction_target,
        "symbol_mapping": symbol_target,
    }


@contextmanager
def runtime_config_environment(config_paths: Mapping[str, str | Path]) -> Iterator[None]:
    """Temporarily expose config files to legacy cached loaders, then restore."""

    previous = {env: os.environ.get(env) for env in _RUNTIME_ENV.values()}
    try:
        for key, env in _RUNTIME_ENV.items():
            path = config_paths.get(key)
            if path is not None:
                os.environ[env] = str(path)
        clear_policy_caches()
        clear_jurisdiction_cache()
        clear_symbol_mapping_cache()
        yield
    finally:
        for env, value in previous.items():
            if value is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = value
        clear_policy_caches()
        clear_jurisdiction_cache()
        clear_symbol_mapping_cache()
