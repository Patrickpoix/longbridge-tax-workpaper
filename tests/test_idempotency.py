from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

from longbridge_tax_workpaper.discovery import find_pdfs
from longbridge_tax_workpaper.runner import run_workpaper

from conftest import make_statement_pdf


def test_two_runs_do_not_reingest_output_pdfs(tmp_path: Path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for month in range(1, 13):
        make_statement_pdf(input_dir / f"statement-monthly-2025{month:02d}-H00000001.pdf", f"2025{month:02d}")
    output_dir = input_dir / "outputs"
    first = run_workpaper(input_dir, output_dir, tax_year=2025, fx_rates={"USD": 7.0, "HKD": 0.9})
    first_hash = first.workbook.read_bytes()
    second = run_workpaper(input_dir, output_dir, tax_year=2025, fx_rates={"USD": 7.0, "HKD": 0.9})
    assert second.workbook.read_bytes() == first_hash
    assert len(find_pdfs(input_dir, exclude_roots=[output_dir])) == 12

    workbook = load_workbook(second.workbook, read_only=True, data_only=True)
    required = {
        "年度纳税汇总", "财产转让计税情景", "FIFO已实现盈亏", "移动平均已实现盈亏",
        "股息与预扣税", "融资利息应计", "融资利息实际支付", "持仓数量对账",
        "期初逐月持仓对账", "月度覆盖", "复核就绪性", "文件追溯", "版本信息",
    }
    assert required.issubset(set(workbook.sheetnames))
    with ZipFile(second.workpapers_zip) as archive:
        names = archive.namelist()
        assert not any("/source_pdfs/" in name for name in names)
        assert any(name.endswith("manifest.json") for name in names)

    archival = run_workpaper(
        input_dir,
        tmp_path / "archival-output",
        tax_year=2025,
        fx_rates={"USD": 7.0, "HKD": 0.9},
        include_source_pdfs=True,
    )
    with ZipFile(archival.workpapers_zip) as archive:
        pdf_names = [name for name in archive.namelist() if "/source_pdfs/" in name and name.endswith(".pdf")]
        assert len(pdf_names) == 12
