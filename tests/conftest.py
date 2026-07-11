from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def make_statement_pdf(path: Path, month: str, *, account: str = "H00000001", password: str | None = None) -> Path:
    year, mon = month[:4], month[4:]
    plain = path.with_suffix(".plain.pdf") if password else path
    c = canvas.Canvas(str(plain), pagesize=(595, 842))
    c.setFont("STSong-Light", 12)
    lines = [
        f"{year}.{mon}", "综合账户月结单", f"{year}.{mon}.28", f"账户编号: {account}",
        "港元 0.00 0.00 0.00 0.00 0.00 0.00 1.000000 0.00",
        "下单时间 成交时间 数量 平均价格", "佣金 平台费 印花税 交易征费", "Page 1 of 1",
    ]
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 24
    c.save()
    if password:
        reader = PdfReader(str(plain))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        with path.open("wb") as fh:
            writer.write(fh)
        plain.unlink()
    return path


@pytest.fixture
def encrypted_statement(tmp_path: Path) -> Path:
    return make_statement_pdf(tmp_path / "statement-monthly-202601-H00000001.pdf", "202601", password="test-password")
