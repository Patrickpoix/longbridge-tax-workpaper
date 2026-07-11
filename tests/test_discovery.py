from pathlib import Path

from longbridge_tax_workpaper.discovery import find_pdfs, split_account_and_year
from longbridge_tax_workpaper.schema import FieldValue, StatementResult


def statement(month: str, account: str = "H123") -> StatementResult:
    item = StatementResult(statement_month=month, source_pdf=f"{month}.pdf")
    item.account["account_id"] = FieldValue.derived(account)
    return item


def test_select_latest_strict_complete_year_and_prior_history():
    rows = [statement(f"2024{month:02d}") for month in range(8, 13)]
    rows += [statement(f"2025{month:02d}") for month in range(1, 13)]
    rows += [statement("202601")]
    year, account, primary, prior = split_account_and_year(rows)
    assert year == 2025
    assert account == "H123"
    assert [x.statement_month for x in primary] == [f"2025{m:02d}" for m in range(1, 13)]
    assert len(prior) == 5


def test_twelve_arbitrary_months_are_not_a_complete_year():
    import pytest

    rows = [statement(f"2025{m:02d}") for m in range(2, 13)] + [statement("202601")]
    with pytest.raises(ValueError, match="没有任何年份包含完整1月至12月"):
        split_account_and_year(rows)

    year, _, primary, _ = split_account_and_year(rows, tax_year=2026)
    assert year == 2026
    assert [x.statement_month for x in primary] == ["202601"]


def test_find_pdfs_excludes_output_and_deduplicates(tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = input_dir / "outputs"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "a.pdf").write_bytes(b"same")
    (input_dir / "copy.pdf").write_bytes(b"same")
    (output_dir / "source.pdf").write_bytes(b"output")
    found = find_pdfs(input_dir, exclude_roots=[output_dir])
    assert len(found) == 1
    assert found[0].name == "a.pdf"
