from pathlib import Path

from longbridge_tax_workpaper.release_hygiene import (
    forbidden_release_paths,
    sensitive_text_findings,
)


def test_release_source_has_no_sensitive_text():
    root = Path(__file__).parents[1]
    assert sensitive_text_findings(root) == []


def test_release_hygiene_scans_the_actual_root(tmp_path: Path):
    (tmp_path / "scripts" / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "scripts" / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"cache")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / "scripts" / "package.egg-info").mkdir()
    (tmp_path / "dist").mkdir()
    found = {path.relative_to(tmp_path).as_posix() for path in forbidden_release_paths(tmp_path)}
    assert "scripts/pkg/__pycache__" in found
    assert "scripts/pkg/__pycache__/x.pyc" in found
    assert ".pytest_cache" in found
    assert "scripts/package.egg-info" in found
    assert "dist" in found


def test_release_hygiene_blocks_statement_and_delivery_artifacts(tmp_path: Path):
    for name in (
        "statement.pdf",
        "result.xlsx",
        "export.csv",
        "delivery.zip",
        ".env",
        ".env.local",
        "private.key",
    ):
        (tmp_path / name).write_bytes(b"x")
    found = {path.relative_to(tmp_path).as_posix() for path in forbidden_release_paths(tmp_path)}
    assert {
        "statement.pdf",
        "result.xlsx",
        "export.csv",
        "delivery.zip",
        ".env",
        ".env.local",
        "private.key",
    } <= found


def test_sensitive_scan_detects_account_like_value_without_committing_one(tmp_path: Path):
    token = "H" + "12345678"
    (tmp_path / "sample.txt").write_text(token, encoding="utf-8")
    assert sensitive_text_findings(tmp_path)


def test_public_synthetic_account_sentinel_is_allowed(tmp_path: Path):
    (tmp_path / "sample.txt").write_text("H00000001", encoding="utf-8")
    assert sensitive_text_findings(tmp_path) == []


def test_private_runtime_denylist(tmp_path: Path, monkeypatch):
    private_token = "PRIVATE_" + "SENTINEL"
    monkeypatch.setenv("LONGBRIDGE_RELEASE_BLOCKED_TOKENS", private_token)
    (tmp_path / "sample.md").write_text(private_token, encoding="utf-8")
    assert sensitive_text_findings(tmp_path)
