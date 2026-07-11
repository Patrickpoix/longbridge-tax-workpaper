from __future__ import annotations

import hashlib
import os
from pathlib import Path
from zipfile import ZipFile

from longbridge_tax_workpaper.archive_determinism import write_deterministic_zip
from longbridge_tax_workpaper.config import prepare_runtime_config, runtime_config_environment
from longbridge_tax_workpaper.hashing import sha256_file
from longbridge_tax_workpaper.symbol_mapping import resolve_symbol_alias
from longbridge_tax_workpaper.cost_basis import canonical_security_id


def test_streaming_hash_and_zip_do_not_use_path_read_bytes(tmp_path: Path, monkeypatch):
    source = tmp_path / "large.bin"
    block = b"0123456789abcdef" * 4096
    with source.open("wb") as fh:
        for _ in range(128):
            fh.write(block)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def fail_read_bytes(self):  # pragma: no cover - only called if implementation regresses
        raise MemoryError("whole-file buffering is forbidden")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    assert sha256_file(source) == expected
    folder = tmp_path / "folder"
    folder.mkdir()
    target = folder / source.name
    target.write_bytes(block)
    archive = tmp_path / "archive.zip"
    write_deterministic_zip(archive, folder, archive_root_name="folder")
    with ZipFile(archive) as zf:
        assert zf.read("folder/large.bin") == block


def test_runtime_environment_is_restored(tmp_path: Path):
    paths = prepare_runtime_config(tmp_path / "config", tax_year=2025, account_opening_month="202501", fx_rates={"USD": 7, "HKD": 0.9})
    original = os.environ.get("LONGBRIDGE_TAX_POLICY_PATH")
    os.environ["LONGBRIDGE_TAX_POLICY_PATH"] = "sentinel-before"
    try:
        with runtime_config_environment(paths):
            assert os.environ["LONGBRIDGE_TAX_POLICY_PATH"] == str(paths["policy"])
        assert os.environ["LONGBRIDGE_TAX_POLICY_PATH"] == "sentinel-before"
    finally:
        if original is None:
            os.environ.pop("LONGBRIDGE_TAX_POLICY_PATH", None)
        else:
            os.environ["LONGBRIDGE_TAX_POLICY_PATH"] = original


def test_symbol_mapping_is_exact_and_auditable():
    assert resolve_symbol_alias("农业银行") == "HK:01288"
    assert resolve_symbol_alias("农业银行额外未知文本") is None
    assert resolve_symbol_alias("完全未知证券") is None
    assert canonical_security_id("Apple Incorporated") == "NAME:appleincorporated"
    assert canonical_security_id("AAPL Apple Inc") == "US:AAPL"
