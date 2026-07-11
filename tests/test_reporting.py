from pathlib import Path

from openpyxl import load_workbook

from longbridge_tax_workpaper.cost_basis import MethodResult
from longbridge_tax_workpaper.reporting import build_processed_workbook
from longbridge_tax_workpaper.schema import StatementResult


def test_single_workbook_multiple_sheets(tmp_path: Path):
    statement = StatementResult(statement_month="202701", source_pdf="sample.pdf")
    report = {"fifo": MethodResult(method="FIFO"), "moving_average": MethodResult(method="MOVING_AVERAGE"), "differences": [], "opening_lots": []}
    target = tmp_path / "result.xlsx"
    build_processed_workbook(
        target, tax_year=2027, account_id="H123", statements=[statement], prior_statements=[], cost_report=report,
        dividends=[], margin_accrual=[], margin_actual=[], readiness={"status": "READY_FOR_REVIEW", "ready_to_file": False, "checks": []}, source_files=[],
    )
    workbook = load_workbook(target, read_only=True, data_only=False)
    assert "年度纳税汇总" in workbook.sheetnames
    assert "FIFO已实现盈亏" in workbook.sheetnames
    assert "移动平均已实现盈亏" in workbook.sheetnames
    assert "复核就绪性" in workbook.sheetnames
    assert len(workbook.sheetnames) > 10
