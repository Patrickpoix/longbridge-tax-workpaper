from __future__ import annotations

from datetime import date
from typing import Iterable

from .filing_policy import (
    is_pending_review_category,
    load_tax_policy,
    load_taxpayer_profile,
    missing_year_end_fx_currencies,
)
from .jurisdiction import load_instrument_jurisdiction
from .schema import FieldValue, SectionResult, StatementResult


def _value(row: dict[str, FieldValue], name: str):
    return row.get(name, FieldValue.missing()).value


def _meta_value(metadata: dict[str, FieldValue], name: str):
    field = metadata.get(name)
    return field.value if field is not None else None


# Recognized templates always score >= 8 (3 base anchors + >=2 content anchors).
# A recognized parse below this floor signals a sparse/degraded text layer that
# should be reviewed by a human rather than trusted silently.
MIN_TRUSTED_TEMPLATE_SCORE = 8


def _tax_year(statements: list[StatementResult], profile: dict[str, object], policy: dict[str, object]) -> int:
    configured = profile.get("tax_year") or policy.get("tax_year")
    if configured:
        return int(configured)
    years = {int(item.statement_month[:4]) for item in statements if str(item.statement_month)[:4].isdigit()}
    if len(years) == 1:
        return years.pop()
    raise ValueError("Unable to infer a single tax year from statements")


def assess_filing_readiness(
    statements: Iterable[StatementResult],
    *,
    cost_report: dict[str, object] | None = None,
    as_of: date | None = None,
) -> dict[str, object]:
    """Assess technical completeness and review risk, never filing legality."""

    statements_list = sorted(list(statements), key=lambda item: item.statement_month)
    policy = load_tax_policy()
    profile = load_taxpayer_profile()
    tax_year = _tax_year(statements_list, profile, policy)
    as_of = as_of or date.today()
    checks: list[dict[str, object]] = []

    def add(code: str, label: str, status: str, blocking: bool, detail: str, *, risk: str = "technical") -> None:
        checks.append({
            "code": code,
            "label": label,
            "status": status,
            "blocking": blocking,
            "risk_type": risk,
            "detail": detail,
        })

    months = [item.statement_month for item in statements_list]
    expected = [f"{tax_year}{month:02d}" for month in range(1, 13)]
    complete = months == expected
    add(
        "MONTHLY_COVERAGE",
        f"{tax_year}年月结单覆盖",
        "PASS" if complete else "BLOCKED",
        not complete,
        f"actual={months}; expected={expected}",
    )

    monthly_errors = [
        f"{statement.statement_month}:{validation.rule}"
        for statement in statements_list
        for validation in statement.validations
        if validation.severity == "error" and not validation.passed
    ]
    add(
        "MONTHLY_VALIDATION",
        "月度解析严格校验",
        "PASS" if not monthly_errors else "BLOCKED",
        bool(monthly_errors),
        "无阻断级解析错误" if not monthly_errors else ", ".join(monthly_errors),
    )

    low_confidence_templates: list[str] = []
    for statement in statements_list:
        meta = statement.metadata
        source = str(_meta_value(meta, "template_recognition_source") or "native_text")
        template_id = str(_meta_value(meta, "template_id") or "unknown_template")
        raw_score = _meta_value(meta, "template_signature_score")
        try:
            score = int(raw_score) if raw_score is not None else 0
        except (TypeError, ValueError):
            score = 0
        if source == "ocr_assisted" or template_id == "unknown_template" or score < MIN_TRUSTED_TEMPLATE_SCORE:
            low_confidence_templates.append(
                f"{statement.statement_month}:template={template_id},source={source},score={score}"
            )
    add(
        "TEMPLATE_RECOGNITION",
        "月结单版式识别置信度",
        "PASS" if not low_confidence_templates else "WARNING",
        False,
        "所有月结单均以高置信度的原生文本层识别" if not low_confidence_templates
        else "以下月份通过OCR回退或版式置信度偏低，建议人工复核表头与数值映射：" + "; ".join(low_confidence_templates),
        risk="technical",
    )

    unresolved_rows: list[dict[str, object]] = []
    for statement in statements_list:
        section = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
        for index, row in enumerate(section.rows, start=1):
            category = str(_value(row, "tax_category") or "")
            amount = _value(row, "cash_amount") if "cash_amount" in row else _value(row, "amount")
            if is_pending_review_category(category) and amount not in (None, 0, 0.0):
                unresolved_rows.append({
                    "statement_month": statement.statement_month,
                    "row_index": index,
                    "category": category,
                    "amount": amount,
                    "currency": _value(row, "currency"),
                })
    add(
        "PENDING_REVIEW_ROWS",
        "未归类现金流水",
        "PASS" if not unresolved_rows else "WARNING",
        False,
        "无未归类现金流水" if not unresolved_rows else f"存在{len(unresolved_rows)}条待复核流水",
        risk="tax_treatment",
    )

    report = cost_report or {}
    report_errors = list(report.get("errors", [])) if isinstance(report, dict) else ["cost report missing"]
    add(
        "COST_BASIS_ENGINE",
        "跨月成本基础与处置匹配",
        "PASS" if report and not report_errors else "BLOCKED",
        not report or bool(report_errors),
        "先进先出法与移动加权平均法成本账已生成" if report and not report_errors else "; ".join(report_errors or ["missing"]),
    )

    prior_coverage = report.get("prior_period_coverage", {}) if isinstance(report, dict) else {}
    prior_ok = prior_coverage.get("status") == "ok"
    add(
        "PRIOR_PERIOD_COVERAGE",
        "期初成本历史覆盖",
        "PASS" if prior_ok else "WARNING",
        False,
        f"actual={prior_coverage.get('actual_months')}; expected={prior_coverage.get('expected_months')}; events={prior_coverage.get('event_count')}",
    )
    monthly_prior_errors = int(prior_coverage.get("monthly_reconciliation_error_count") or 0)
    monthly_prior_rows = list(prior_coverage.get("monthly_reconciliation") or [])
    monthly_prior_status = str(prior_coverage.get("monthly_reconciliation_status") or "missing")
    monthly_prior_ok = monthly_prior_status in {"ok", "not_applicable"}
    add(
        "PRIOR_MONTHLY_POSITION_RECONCILIATION",
        "税前年份逐月持仓滚动对账",
        "PASS" if prior_ok and monthly_prior_ok else "WARNING",
        False,
        (
            f"rows={len(monthly_prior_rows)}; differences=0"
            if prior_ok and monthly_prior_status == "ok" and monthly_prior_errors == 0
            else "本年度无须税前年份期初成本重建"
            if prior_ok and monthly_prior_status == "not_applicable"
            else f"rows={len(monthly_prior_rows)}; error_count={monthly_prior_errors}; prior_coverage_status={prior_coverage.get('status')}; monthly_status={monthly_prior_status}"
        ),
    )

    opening_rows = list(report.get("opening_lots", [])) if isinstance(report, dict) else []
    invalid_opening = [
        row for row in opening_rows
        if float(row.get("quantity") or 0.0) <= 0 or float(row.get("total_cost") or 0.0) <= 0
    ]
    unverified_opening = [
        row for row in opening_rows
        if row.get("evidence_status") != "verified_from_complete_prior_trade_ledger"
    ]
    add(
        "OPENING_COST_VALIDITY",
        "期初成本数值有效性",
        "PASS" if not invalid_opening else "BLOCKED",
        bool(invalid_opening),
        "期初成本均为正" if not invalid_opening else f"invalid={[(row.get('method'), row.get('security_id')) for row in invalid_opening]}",
    )
    add(
        "OPENING_COST_EVIDENCE",
        "期初成本证据",
        "PASS" if not unverified_opening else "WARNING",
        False,
        "期初批次由税年前历史月结单重建" if not unverified_opening else "部分期初批次仅有月结单展示成本或缺少完整历史",
    )

    method = profile.get("cost_basis_method", {})
    selected_method = method.get("selected_method")
    method_confirmed = selected_method in {"FIFO", "MOVING_AVERAGE"} and method.get("status") == "confirmed"
    add(
        "COST_METHOD_CONFIRMATION",
        "最终成本配对方法",
        "PASS" if method_confirmed else "WARNING",
        False,
        f"selected_method={selected_method}; 两种方法均保留",
        risk="tax_treatment",
    )

    loss_rule = policy.get("property_transfer_loss_offset", {})
    authority_documented = loss_rule.get("authority_status") == "documented_tax_authority_or_professional_opinion"
    add(
        "PROPERTY_TRANSFER_LOSS_OFFSET",
        "证券已实现盈亏抵减口径",
        "PASS" if authority_documented else "WARNING",
        False,
        str(loss_rule.get("note") or "系统并列输出不同抵减情景"),
        risk="tax_treatment",
    )

    margin_rule = policy.get("category_rules", {}).get("margin_interest_deductible", {})
    margin_final = margin_rule.get("deductible_in_final_filing") is False
    add(
        "MARGIN_INTEREST_TAX_TREATMENT",
        "融资利息处理",
        "PASS" if margin_final else "WARNING",
        False,
        "默认不扣除；应计与实际支付仅作审计列示" if margin_final else "处理口径待确认",
        risk="tax_treatment",
    )

    jurisdiction = load_instrument_jurisdiction()
    fifo_result = report.get("fifo") if isinstance(report, dict) else None
    realized_security_ids = {
        str(row.get("security_id"))
        for row in getattr(fifo_result, "disposals", [])
        if row.get("security_id")
    }
    unresolved = sorted(
        security_id for security_id in realized_security_ids
        if jurisdiction.get(security_id, {}).get("source_classification_status")
        not in {"complete", "complete_user_position", "mapped", "not_applicable_current_year"}
    )
    add(
        "SOURCE_JURISDICTION",
        "发行人或合约来源信息",
        "PASS" if not unresolved else "WARNING",
        False,
        "影响当年处置的证券均有映射" if not unresolved else f"待补来源信息={unresolved}",
        risk="tax_treatment",
    )

    tax_resident = profile.get("tax_residency", {}).get("is_china_tax_resident") is True
    add(
        "TAX_RESIDENCY",
        "中国税收居民身份",
        "PASS" if tax_resident else "WARNING",
        False,
        "默认面向中国内地税收居民" if tax_resident else "税收居民身份未确认",
        risk="taxpayer_fact",
    )

    missing_fx = missing_year_end_fx_currencies(policy)
    add(
        "YEAR_END_FX",
        f"{tax_year}-12-31人民币汇率中间价",
        "PASS" if not missing_fx else "BLOCKED",
        bool(missing_fx),
        (
            f"USD={policy.get('year_end_fx_rates', {}).get('USD', {}).get('rate')}; "
            f"HKD={policy.get('year_end_fx_rates', {}).get('HKD', {}).get('rate')}"
            if not missing_fx
            else f"缺少汇率={missing_fx}；人民币输出留空，未以0代替"
        ),
    )

    blocking = [item for item in checks if item["blocking"] and item["status"] != "PASS"]
    warnings = [item for item in checks if item["status"] == "WARNING"]
    if blocking:
        status = "BLOCKED_FOR_REVIEW"
    elif warnings:
        status = "REVIEW_REQUIRED"
    else:
        status = "TECHNICALLY_GENERATED"
    return {
        "status": status,
        "review_status": status,
        "ready_to_file": False,
        "ready_for_review": not blocking,
        "tax_year": tax_year,
        "as_of": as_of.isoformat(),
        "blocking_reasons": [str(item["detail"]) for item in blocking],
        "warning_reasons": [str(item["detail"]) for item in warnings],
        "checks": checks,
        "pending_review_rows": unresolved_rows,
        "taxpayer_profile_version": profile.get("profile_version"),
        "policy_version": policy.get("policy_version"),
        "notes": [
            "本工具生成税务工作底稿，不替代主管税务机关或专业税务意见。",
            "只统计已实现盈亏，未实现盈亏不纳入。",
            "先进先出法与移动加权平均法并列输出。",
            "月结单扣税作为抵免候选，是否获准抵免以主管机关审核为准。",
            "TECHNICALLY_GENERATED/REVIEW_REQUIRED/BLOCKED_FOR_REVIEW均不表示可直接申报。",
        ],
    }
