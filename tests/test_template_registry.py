from pathlib import Path

import pytest

from longbridge_tax_workpaper.ingest import IngestedDocument, PageData, load_pdf
from longbridge_tax_workpaper.template_registry import UnknownStatementTemplateError, detect_template
from longbridge_tax_workpaper.pipeline import parse_statement


def document(text: str) -> IngestedDocument:
    return IngestedDocument(path=None, pages=[PageData(1, 595, 842, text, text, [])])


def test_unknown_template_is_not_silently_accepted():
    result = detect_template(document("ordinary unrelated PDF"))
    assert result.template_id == "unknown_template"
    assert result.recognized is False


def test_encrypted_known_template_is_recognized(encrypted_statement):
    loaded = load_pdf(encrypted_statement, password="test-password")
    result = detect_template(loaded)
    assert result.recognized is True
    statement = parse_statement(encrypted_statement, password="test-password")
    assert statement.statement_month == "202601"


def test_wrong_password_fails(encrypted_statement):
    with pytest.raises(ValueError, match="无法打开PDF|PDF密码错误或缺失"):
        load_pdf(encrypted_statement, password="wrong")


def test_header_aliases_tolerate_minor_future_wording_changes():
    text = """
    长桥证券 综合帐户月结单 2027-12
    帐户编号: H00000001
    账户概览 美元 港元
    委托时间 执行时间 成交均价
    经纪佣金
    Page 1 of 2
    """
    result = detect_template(document(text))
    assert result.recognized is True
    assert result.features["has_statement_title"] is True
    assert result.features["has_account_overview_anchor"] is True
    assert result.features["has_trade_detail_anchor"] is True


def test_ocr_text_can_rescue_unreadable_embedded_font(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "statement-monthly-202712-H00000001.pdf"
    pdf_path.write_bytes(b"synthetic")
    unreadable = IngestedDocument(
        path=pdf_path,
        pages=[PageData(1, 595, 842, "□□□", "□□□", [])],
        password=None,
    )
    ocr_text = """
    综合账户月结单 2027.12
    账户编号: H00000001
    账户总览 美元 港元
    下单时间 成交时间 平均价格 佣金
    Page 1 of 1
    """
    monkeypatch.setattr("longbridge_tax_workpaper.pipeline.load_pdf", lambda *_args, **_kwargs: unreadable)
    monkeypatch.setattr(
        "longbridge_tax_workpaper.extractors.ocr.extract_document_ocr_text",
        lambda _document, page_numbers=None: {1: ocr_text},
    )

    statement = parse_statement(pdf_path, enable_ocr=True)

    assert statement.statement_month == "202712"
    assert statement.metadata["template_recognition_source"].value == "ocr_assisted"
    assert statement.metadata["ingest_source"].value.endswith("+ocr")
