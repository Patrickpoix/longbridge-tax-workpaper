from pathlib import Path

from longbridge_tax_workpaper.extractors.native.cash_flows import extract_other_fund_flows
from longbridge_tax_workpaper.extractors.native.trades import extract_trade_sections
from longbridge_tax_workpaper.ingest import IngestedDocument, PageData


def document(text: str) -> IngestedDocument:
    return IngestedDocument(Path("synthetic.pdf"), [PageData(1, 595, 842, text, text, [])])


def values(row):
    return {key: value.value for key, value in row.items()}


def test_stock_and_option_trade_extraction_with_fees_and_currency():
    text = """
2025.01.02 2025.01.02 OS123456 买入 1288 农业银行 100 4.0000 400.00 -405.00 佣金 0.00 平台费 5.00 印花税 0.00 10:00:00 HKT 10:00:01 HKT
2025.01.03 2025.01.03 OS123457 卖出 AAPL250117C200 1 2.0000 200.00 197.00 佣金 1.00 期权清算费 2.00 10:00:00 EST 10:00:01 EST
"""
    stock, option = extract_trade_sections(document(text))
    assert len(stock.rows) == 1
    assert len(option.rows) == 1
    stock_row = values(stock.rows[0])
    option_row = values(option.rows[0])
    assert stock_row["side"] == "BUY"
    assert stock_row["currency"] == "HKD"
    assert stock_row["total_amount"] == -405.0
    assert stock_row["platform_fee"] == 5.0
    assert option_row["side"] == "SELL"
    assert option_row["currency"] == "USD"
    assert option_row["total_amount"] == 197.0
    assert option_row["clearing_fee"] == 2.0
    assert stock.fields["unmatched_order_ids"].value == []


def test_cash_flow_currency_block_and_unlabeled_interest_behavior():
    grouped = document(
        "其他资金出入明细 币种:港元 "
        "2025.01.15 现金分红 #1288.HK RMB0.20/SH Held:100 (-10%) 18.00 "
        "汇总(HKD) 18.00 责任说明"
    )
    row = values(extract_other_fund_flows(grouped).rows[0])
    assert row["cash_amount"] == 18.0
    assert row["currency"] == "HKD"
    assert row["tax_category"] == "dividend_income"

    legacy = document("其他资金出入明细 2025.01.31 贷款利息 -5.00 责任说明")
    legacy_row = values(extract_other_fund_flows(legacy).rows[0])
    assert legacy_row["cash_amount"] == -5.0
    assert legacy_row["currency"] is None
    assert legacy_row["tax_category"] == "margin_interest_deductible"
