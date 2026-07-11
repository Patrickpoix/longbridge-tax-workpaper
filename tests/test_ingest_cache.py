import hashlib
import json
from pathlib import Path

from longbridge_tax_workpaper.ingest import _load_pdf_sidecar


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sidecar_requires_matching_pdf_hash(tmp_path: Path):
    pdf = tmp_path / "statement-monthly-202601-H00000001.pdf"
    pdf.write_bytes(b"not-a-real-pdf-but-hashable")
    folder = tmp_path / "pdf_extracts" / "2026-01"
    folder.mkdir(parents=True)
    (folder / "page_001_text.txt").write_text("cached", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"source_pdf_name": pdf.name, "source_pdf_sha256": "wrong"}), encoding="utf-8")
    assert _load_pdf_sidecar(pdf, None) is None
    (folder / "manifest.json").write_text(json.dumps({"source_pdf_name": pdf.name, "source_pdf_sha256": digest(pdf)}), encoding="utf-8")
    loaded = _load_pdf_sidecar(pdf, None)
    assert loaded is not None
    assert loaded.ingest_source == "pdf_extracts_verified"


def test_sidecar_requires_matching_pdf_filename_even_when_hash_matches(tmp_path: Path):
    pdf = tmp_path / "statement-monthly-202601-H00000001.pdf"
    pdf.write_bytes(b"same-content")
    folder = tmp_path / "pdf_extracts" / "2026-01"
    folder.mkdir(parents=True)
    (folder / "page_001_text.txt").write_text("cached", encoding="utf-8")
    (folder / "manifest.json").write_text(
        json.dumps({"source_pdf_name": "different-account.pdf", "source_pdf_sha256": digest(pdf)}),
        encoding="utf-8",
    )
    assert _load_pdf_sidecar(pdf, None) is None
