from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .archive_determinism import write_deterministic_zip
from .config import prepare_runtime_config, runtime_config_environment
from .cost_basis import build_cost_basis_report
from .discovery import find_pdfs, parse_pdf_set, split_account_and_year
from .dividends import build_dividend_tax_basis_rows
from .filing_readiness import assess_filing_readiness
from .hashing import sha256_file
from .margin_interest import build_margin_interest_actual_payment_rows, build_margin_interest_hkd_basis_rows
from .postprocess import resolve_cross_month_statement_context
from .reporting import build_processed_workbook
from .serialization import section_rows, write_csv, write_statement_json


@dataclass(slots=True)
class RunResult:
    tax_year: int
    account_id: str | None
    workbook: Path
    workpapers_zip: Path
    processed_delivery_zip: Path
    review_status: Path
    output_dir: Path


def _manifest(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {"package_version": "longbridge-tax-workpaper-v4", "files": files}


def _csv_rows_from_report(cost_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    fifo = cost_report.get("fifo")
    moving = cost_report.get("moving_average")
    return {
        "cost_basis_opening_lots.csv": list(cost_report.get("opening_lots", [])),
        "cost_basis_events.csv": list(cost_report.get("events", [])),
        "realized_pnl_fifo.csv": list(getattr(fifo, "disposals", [])),
        "realized_pnl_moving_average.csv": list(getattr(moving, "disposals", [])),
        "realized_pnl_method_difference.csv": list(cost_report.get("differences", [])),
        "remaining_lots_fifo.csv": list(getattr(fifo, "remaining_lots", [])),
        "remaining_lots_moving_average.csv": list(getattr(moving, "remaining_lots", [])),
        "position_reconciliation_fifo.csv": list(getattr(fifo, "reconciliation", [])),
        "position_reconciliation_moving_average.csv": list(getattr(moving, "reconciliation", [])),
        "prior_monthly_position_reconciliation.csv": list(
            cost_report.get("prior_period_coverage", {}).get("monthly_reconciliation", [])
        ),
        "realized_pnl_summary.csv": list(cost_report.get("summary", [])),
    }


def _workpaper_readme(tax_year: int, status: str, *, includes_source_pdfs: bool) -> str:
    privacy = (
        "本底稿包包含原始券商月结单PDF，属于高度敏感财务资料；请仅本地留档，不要发送给无关第三方。"
        if includes_source_pdfs
        else "本底稿包未复制原始PDF；文件追溯表仍保留文件名与SHA-256。"
    )
    return f"""# 长桥证券 {tax_year} 年度税务工作底稿

- 输入：同一长桥账户的月结单PDF。
- 输出：一个多工作表Excel，以及本独立底稿目录。
- 适用范围：中国内地税收居民、单一长桥证券账户。
- 复核状态：{status}。
- 隐私提示：{privacy}
- 边界：本工具生成工作底稿，不替代主管税务机关或专业税务意见。
"""


def run_workpaper(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    password: str | None = None,
    tax_year: int | None = None,
    account_id: str | None = None,
    fx_rates: dict[str, str] | None = None,
    fx_metadata: dict[str, dict[str, Any]] | None = None,
    policy_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    jurisdiction_path: str | Path | None = None,
    symbol_mapping_path: str | Path | None = None,
    enable_ocr: bool = True,
    include_source_pdfs: bool = False,
    cost_basis_method: str = "BOTH",
    withholding_credit: bool = False,
    deduct_margin_interest: bool = False,
) -> RunResult:
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs = find_pdfs(input_dir, exclude_roots=[output_dir])
    if not pdfs:
        raise FileNotFoundError(f"输入目录未找到PDF: {input_dir}")

    statements_all = resolve_cross_month_statement_context(
        parse_pdf_set(pdfs, password=password, enable_ocr=enable_ocr)
    )
    selected_year, selected_account, statements, prior_statements = split_account_and_year(
        statements_all, tax_year=tax_year, account_id=account_id
    )
    opening_month = prior_statements[0].statement_month if prior_statements else statements[0].statement_month
    config_paths = prepare_runtime_config(
        output_dir / "runtime_config",
        tax_year=selected_year,
        account_opening_month=opening_month,
        fx_rates=fx_rates,
        fx_metadata=fx_metadata,
        policy_path=policy_path,
        profile_path=profile_path,
        jurisdiction_path=jurisdiction_path,
        symbol_mapping_path=symbol_mapping_path,
        cost_basis_method=cost_basis_method,
        withholding_credit=withholding_credit,
        deduct_margin_interest=deduct_margin_interest,
    )

    workpapers = output_dir / f"longbridge_{selected_year}_workpapers"
    delivery = output_dir / f"longbridge_{selected_year}_processed_delivery"
    for folder in (workpapers, delivery):
        if folder.exists():
            shutil.rmtree(folder)
    (workpapers / "monthly_json").mkdir(parents=True, exist_ok=True)
    (workpapers / "tables").mkdir(parents=True, exist_ok=True)
    (workpapers / "config").mkdir(parents=True, exist_ok=True)
    if include_source_pdfs:
        (workpapers / "source_pdfs").mkdir(parents=True, exist_ok=True)

    selected_all = sorted(prior_statements + statements, key=lambda item: item.statement_month)
    source_files: list[dict[str, Any]] = []
    for statement in selected_all:
        json_path = workpapers / "monthly_json" / f"{statement.statement_month}.json"
        write_statement_json(statement, json_path)
        src = Path(statement.source_pdf)
        source_files.append({
            "月份": statement.statement_month,
            "文件名": src.name,
            "SHA-256": sha256_file(src),
            "角色": "纳税年度月结单" if statement in statements else "期初成本历史证据",
        })
        if include_source_pdfs:
            shutil.copy2(src, workpapers / "source_pdfs" / src.name)

    with runtime_config_environment(config_paths):
        cost_report = build_cost_basis_report(statements, prior_statements)
        dividends = build_dividend_tax_basis_rows(statements)
        margin_accrual = build_margin_interest_hkd_basis_rows(statements)
        margin_actual = build_margin_interest_actual_payment_rows(statements)
        readiness = assess_filing_readiness(statements, cost_report=cost_report)

        tables = _csv_rows_from_report(cost_report)
        tables.update({
            "dividend_tax_basis.csv": dividends,
            "margin_interest_accrual_hkd.csv": margin_accrual,
            "margin_interest_actual_payment_hkd.csv": margin_actual,
            "all_stock_trades.csv": section_rows(statements, "stock_trades"),
            "all_option_trades.csv": section_rows(statements, "option_trades"),
            "all_fund_flows.csv": section_rows(statements, "other_fund_flows"),
            "all_holdings.csv": section_rows(statements, "holdings"),
            "validation_summary.csv": list(readiness.get("checks", [])),
        })
        for filename, rows in tables.items():
            write_csv(workpapers / "tables" / filename, rows)

        review_status_path = workpapers / "review_status.json"
        review_status_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8")

        workbook_path = output_dir / f"longbridge_{selected_year}_processed_results.xlsx"
        build_processed_workbook(
            workbook_path,
            tax_year=selected_year,
            account_id=selected_account,
            statements=statements,
            prior_statements=prior_statements,
            cost_report=cost_report,
            dividends=dividends,
            margin_accrual=margin_accrual,
            margin_actual=margin_actual,
            readiness=readiness,
            source_files=source_files,
        )

    for source in config_paths.values():
        shutil.copy2(source, workpapers / "config" / source.name)
    shutil.copy2(workbook_path, workpapers / workbook_path.name)

    readme = _workpaper_readme(selected_year, str(readiness.get("status")), includes_source_pdfs=include_source_pdfs)
    (workpapers / "README.md").write_text(readme, encoding="utf-8")
    (workpapers / "manifest.json").write_text(
        json.dumps(_manifest(workpapers), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    workpapers_zip = output_dir / f"longbridge_{selected_year}_workpapers.zip"
    write_deterministic_zip(workpapers_zip, workpapers, archive_root_name=workpapers.name)

    delivery.mkdir(parents=True)
    shutil.copy2(workbook_path, delivery / workbook_path.name)
    shutil.copy2(review_status_path, delivery / "review_status.json")
    delivery_readme = readme + "\n对外审阅优先使用本精简包；完整底稿包可能包含敏感原始PDF。\n"
    (delivery / "README.md").write_text(delivery_readme, encoding="utf-8")
    (delivery / "manifest.json").write_text(
        json.dumps(_manifest(delivery), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    processed_zip = output_dir / f"longbridge_{selected_year}_processed_delivery.zip"
    write_deterministic_zip(processed_zip, delivery, archive_root_name=delivery.name)

    external_status = output_dir / f"review_status_{selected_year}.json"
    shutil.copy2(review_status_path, external_status)
    return RunResult(
        tax_year=selected_year,
        account_id=selected_account,
        workbook=workbook_path,
        workpapers_zip=workpapers_zip,
        processed_delivery_zip=processed_zip,
        review_status=external_status,
        output_dir=output_dir,
    )
