from __future__ import annotations

import gc
import re
from dataclasses import replace
from pathlib import Path

from .extractors.native import (
    extract_account_overview,
    extract_other_fund_flows,
    extract_portfolio_sections,
    extract_trade_sections,
)
from .hashing import sha256_file
from .ingest import IngestedDocument, load_pdf
from .normalize import normalize_text
from .schema import FieldValue, SectionResult, StatementResult, ValidationResult
from .template_registry import UnknownStatementTemplateError, detect_template, text_layer_requires_ocr
from .validate import validate_statement

MONTH_RE = re.compile(r"(?<!\d)(20\d{4})(?!\d)")
SCHEMA_VERSION = "longbridge-tax-workpaper-schema-v2"
PARSER_VERSION = "longbridge-tax-workpaper-parser-v2"

PDF_MONTH_RE = re.compile(r"\b(20\d{2})\.(\d{2})\b")
PDF_DATE_RE = re.compile(r"\b(20\d{2})\.(\d{2})\.(\d{2})\b")
ACCOUNT_ID_RE = re.compile(r"账[户戶]编号:\s*([A-Z]\d+)", re.IGNORECASE)


def _pdf_header_metadata(document: IngestedDocument) -> dict[str, FieldValue]:
    if not document.pages:
        return {}
    raw_text = document.pages[0].text
    text = normalize_text(raw_text)
    metadata: dict[str, FieldValue] = {}

    month_match = PDF_MONTH_RE.search(text)
    if month_match:
        metadata["statement_month_from_pdf"] = FieldValue.native(
            f"{month_match.group(1)}{month_match.group(2)}",
            raw_text=month_match.group(0),
            page=1,
            confidence=0.96,
        )

    date_match = PDF_DATE_RE.search(text)
    if date_match:
        metadata["statement_date_from_pdf"] = FieldValue.native(
            f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}",
            raw_text=date_match.group(0),
            page=1,
            confidence=0.96,
        )

    account_match = ACCOUNT_ID_RE.search(text)
    if account_match:
        metadata["account_id"] = FieldValue.native(
            account_match.group(1),
            raw_text=account_match.group(0),
            page=1,
            confidence=0.94,
        )
    else:
        metadata["account_id"] = FieldValue.missing(warnings=["account_id_not_visible_in_header_text"])
    return metadata


def _append_pdf_month_consistency_validation(statement: StatementResult) -> None:
    pdf_month = statement.metadata.get("statement_month_from_pdf")
    if not pdf_month or pdf_month.value is None:
        statement.validations.append(
            ValidationResult(
                rule="pdf_statement_month_consistency",
                passed=True,
                severity="warning",
                message="PDF statement month was not found in header text; filename month is used.",
            )
        )
        return
    passed = str(pdf_month.value) == str(statement.statement_month)
    statement.validations.append(
        ValidationResult(
            rule="pdf_statement_month_consistency",
            passed=passed,
            severity="error" if not passed else "info",
            message=(
                f"PDF header month {pdf_month.value} matches filename month {statement.statement_month}"
                if passed
                else f"PDF header month {pdf_month.value} does not match filename month {statement.statement_month}"
            ),
            details={"pdf_month": pdf_month.value, "filename_month": statement.statement_month},
        )
    )


def _statement_month(path: Path) -> str:
    match = MONTH_RE.search(path.name)
    if match:
        return match.group(1)
    return "unknown"


def _augment_document_with_ocr(
    document: IngestedDocument,
    ocr_text_by_page: dict[int, str],
) -> IngestedDocument:
    pages = []
    for page in document.pages:
        ocr_text = ocr_text_by_page.get(page.page_number, "")
        if not ocr_text:
            pages.append(page)
            continue
        combined = f"{page.text}\n{ocr_text}" if page.text else ocr_text
        pages.append(replace(page, text=combined, normalized_text=normalize_text(combined)))
    return replace(document, pages=pages, ingest_source=f"{document.ingest_source}+ocr")



def parse_statement(path: str | Path, *, password: str | None = None, enable_ocr: bool = True) -> StatementResult:
    pdf_path = Path(path)
    document = load_pdf(pdf_path, password=password)
    template = detect_template(document)
    template_source = "native_text"
    if enable_ocr and (not template.recognized or text_layer_requires_ocr(document)):
        from .extractors.ocr import extract_document_ocr_text

        ocr_text_by_page = extract_document_ocr_text(document)
        if ocr_text_by_page:
            document = _augment_document_with_ocr(document, ocr_text_by_page)
            template = detect_template(document)
            template_source = "ocr_assisted"
    if not template.recognized:
        ocr_hint = (
            " Install the optional OCR dependencies and retry with --enable-ocr."
            if enable_ocr
            else " Retry with --enable-ocr if the PDF text layer uses an unsupported font."
        )
        raise UnknownStatementTemplateError(
            f"无法识别长桥月结单版式: {pdf_path.name}; "
            f"score={template.score}; missing={template.missing_requirements}.{ocr_hint}"
        )
    statement = StatementResult(statement_month=_statement_month(pdf_path), source_pdf=str(pdf_path))
    statement.metadata["template_id"] = FieldValue.derived(template.template_id, confidence=0.95)
    statement.metadata["template_signature_score"] = FieldValue.derived(template.score, confidence=1.0)
    statement.metadata["template_recognition_source"] = FieldValue.derived(template_source, confidence=1.0)
    statement.metadata["schema_version"] = FieldValue.derived(SCHEMA_VERSION, confidence=1.0)
    statement.metadata["parser_version"] = FieldValue.derived(PARSER_VERSION, confidence=1.0)
    statement.metadata["source_pdf_sha256"] = FieldValue.derived(sha256_file(pdf_path), confidence=1.0)
    statement.metadata["source_pdf_name"] = FieldValue.derived(pdf_path.name, confidence=1.0)
    statement.metadata["ocr_enabled"] = FieldValue.derived(bool(enable_ocr), confidence=1.0)
    statement.metadata["pdf_password_supplied"] = FieldValue.derived(bool(password), confidence=1.0)
    statement.metadata["ingest_source"] = FieldValue.derived(document.ingest_source, confidence=1.0)
    for feature_name, feature_value in template.features.items():
        statement.metadata[f"feature_{feature_name}"] = FieldValue.derived(feature_value, confidence=0.9)
    statement.metadata.update(_pdf_header_metadata(document))
    if statement.metadata.get("account_id") is None or statement.metadata.get("account_id").value is None:
        filename_account = re.search(r"-([A-Z]\d+)\.pdf$", pdf_path.name, re.IGNORECASE)
        if filename_account:
            statement.metadata["account_id"] = FieldValue.derived(filename_account.group(1).upper(), raw_text=pdf_path.name, confidence=0.90)
    if statement.statement_month == "unknown":
        pdf_month = statement.metadata.get("statement_month_from_pdf")
        if pdf_month and pdf_month.value:
            statement.statement_month = str(pdf_month.value)
    if "account_id" in statement.metadata:
        statement.account["account_id"] = statement.metadata["account_id"]

    overview, header_financing_limit = extract_account_overview(document)
    statement.sections["account_overview"] = overview
    statement.metadata["header_financing_limit"] = header_financing_limit
    cash_balances, holdings = extract_portfolio_sections(document)
    statement.sections["cash_balances"] = cash_balances
    statement.sections["holdings"] = holdings
    statement.sections["other_fund_flows"] = extract_other_fund_flows(document)
    stock_trades, option_trades = extract_trade_sections(document)
    statement.sections["stock_trades"] = stock_trades
    statement.sections["option_trades"] = option_trades

    _append_native_presence_validations(statement)
    _append_pdf_month_consistency_validation(statement)
    if enable_ocr:
        _apply_ocr_fallback(statement, document)
        # OCR output is retained as provenance.  The current parser does not use
        # OCR text to overwrite native structured fields; re-run validation after
        # OCR so the report reflects the final StatementResult object.
        statement.validations.append(
            ValidationResult(
                rule="ocr_structured_merge",
                passed=True,
                severity="warning",
                message="OCR text is attached as audit provenance only; no OCR-derived structured merge was performed.",
            )
        )

    statement.validations.extend(validate_statement(statement))
    # Do not keep the extracted PageData/word snapshots alive after the
    # StatementResult has been built. This matters when tests or batch jobs parse
    # all twelve PDFs in one Python process.
    del document
    gc.collect()
    return statement


def _append_native_presence_validations(statement: StatementResult) -> None:
    required_sections = [
        "account_overview",
        "cash_balances",
        "holdings",
        "other_fund_flows",
        "stock_trades",
        "option_trades",
    ]
    for section_name in required_sections:
        section = statement.sections.get(section_name)
        present = section is not None
        has_content = bool(section and (section.rows or section.fields))
        statement.validations.append(
            ValidationResult(
                rule=f"native_{section_name}_present",
                passed=present and has_content,
                severity="error" if not (present and has_content) and section_name not in {"stock_trades", "option_trades"} else "info",
                message=f"Native parser produced {section_name} with structured content." if present and has_content else f"Native parser did not produce structured content for {section_name}.",
            )
        )


def _section_needs_ocr(section: SectionResult) -> bool:
    if section.warnings:
        return True
    for value in section.fields.values():
        if value.source == "missing":
            return True
    if not section.rows and section.name not in {"option_trades", "stock_trades", "other_fund_flows"}:
        return True
    return False


def _apply_ocr_fallback(statement: StatementResult, document: IngestedDocument) -> None:
    from .extractors.ocr import extract_section_ocr

    pages_to_ocr: set[int] = set()
    for section_name, section in statement.sections.items():
        if _section_needs_ocr(section):
            if section_name == "account_overview":
                pages_to_ocr.add(1)
            elif section_name in {"cash_balances", "holdings"}:
                pages_to_ocr.update({1, 2})
            elif section_name in {"stock_trades", "option_trades"}:
                pages_to_ocr.add(2)
            elif section_name == "other_fund_flows":
                pages_to_ocr.add(max((page.page_number for page in document.pages), default=1))

    if not pages_to_ocr:
        statement.warnings.append("OCR enabled but no sections needed fallback")
        return

    for page_number in sorted(page for page in pages_to_ocr if 1 <= page <= len(document.pages)):
        result = extract_section_ocr(
            document,
            page_number=page_number,
            section_name=f"ocr_page_{page_number}",
            reason="native_section_missing_or_low_confidence",
        )
        if result.fields.get("ocr_raw_text"):
            statement.sections[f"ocr_page_{page_number}"] = result
            statement.warnings.append(f"OCR fallback applied to page {page_number}")
        else:
            statement.sections[f"ocr_page_{page_number}"] = result
            statement.warnings.append(f"OCR fallback page {page_number} produced no usable text")
