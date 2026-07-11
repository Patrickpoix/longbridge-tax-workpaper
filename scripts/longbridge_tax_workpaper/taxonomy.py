from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

KNOWN_CURRENCIES = ("HKD", "USD")
UNKNOWN_CURRENCY = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TaxCategorySpec:
    code: str
    label: str
    note: str
    cash_tax_summary: bool = True


TAX_CATEGORY_SPECS: tuple[TaxCategorySpec, ...] = (
    TaxCategorySpec("dividend_income", "股息收入", "计入应税所得"),
    TaxCategorySpec("margin_interest_deductible", "融资利息支出（税务处理未确认）", "应计/实际支付审计证据；未确认可扣除"),
    TaxCategorySpec("withholding_tax", "预扣税费用", "境外已缴税额/税收抵免参考，不能直接冲减收入"),
    TaxCategorySpec("service_fee_deductible", "股息相关券商/代处理服务费（审计）", "单独列示，不直接冲减股息所得"),
    TaxCategorySpec("company_action_fee_non_deductible", "公司行动其他费用", "用户确认：单独列示，不纳入抵扣"),
    TaxCategorySpec("derivative_auto_ex_proceeds", "衍生品AUTO-EX结算现金（审计）", "结算现金是已实现盈亏的收入端，不直接作为股息或收入总额重复申报"),
    TaxCategorySpec("derivative_settlement_fee_deductible", "衍生品结算直接处理费（计入盈亏）", "与AUTO-EX结算直接相关，进入该衍生品已实现盈亏计算"),
    TaxCategorySpec("derivative_settlement_fee_non_deductible", "衍生品公司行动费（不抵扣）", "用户确认单独列示，不进入可扣除成本"),
    TaxCategorySpec("cash_reward_other_income", "现金奖励（其他/偶然性质收入候选）", "20%参考税率，分类待确认"),
    TaxCategorySpec("non_taxable_cash_movement", "资金存取", "非应税现金移动"),
    TaxCategorySpec("trading_related", "交易资金变动", "交易相关-详见交易明细"),
    TaxCategorySpec("pending_review", "待确认", "待确认"),
    TaxCategorySpec("non_cash_company_action", "非现金公司行动", "仅审计列示，不计入现金税表口径", cash_tax_summary=False),
)

TAX_CATEGORY_NOTES: dict[str, tuple[str, str]] = {
    spec.code: (spec.label, spec.note) for spec in TAX_CATEGORY_SPECS
}
CASH_TAX_CATEGORIES: tuple[str, ...] = tuple(
    spec.code for spec in TAX_CATEGORY_SPECS if spec.cash_tax_summary
)


def normalize_currency(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in KNOWN_CURRENCIES:
        return text
    return UNKNOWN_CURRENCY


def summary_row_label(category: str, currency: str) -> str:
    label = TAX_CATEGORY_NOTES.get(category, (category or "待确认", "待确认"))[0]
    return f"{label}({currency})"


def iter_cash_summary_rows(present_keys: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for category in CASH_TAX_CATEGORIES:
        for currency in (*KNOWN_CURRENCIES, UNKNOWN_CURRENCY):
            key = (category, currency)
            rows.append((summary_row_label(category, currency), category, currency))
            seen.add(key)

    for category, currency in sorted(present_keys):
        normalized_currency = normalize_currency(currency)
        key = (category, normalized_currency)
        if key in seen:
            continue
        rows.append((summary_row_label(category, normalized_currency), category, normalized_currency))
        seen.add(key)
    return rows
