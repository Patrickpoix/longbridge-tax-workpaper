from pathlib import Path

from longbridge_tax_workpaper.release_hygiene import forbidden_release_paths


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".review",
    "outputs",
    "review_run_outputs",
}
SKIP_SUFFIXES = {".pdf", ".png", ".zip", ".pyc", ".whl", ".gz", ".xlsx", ".xls"}


def _is_scannable_source(path: Path, root: Path) -> bool:
    if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    parts = set(path.relative_to(root).parts)
    if parts & SKIP_DIRS or any(part.endswith(".egg-info") for part in parts):
        return False
    return True


def test_release_source_has_no_private_runtime_or_real_person_name():
    root = Path(__file__).parents[1]
    blocked = ["artifact" + "_tool", "黄" + "品" + "杰", "H108" + "04580", "3231" + "4831"]
    for path in root.rglob("*"):
        if not _is_scannable_source(path, root):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in blocked:
            assert token not in text, f"sensitive/private token found in {path}"


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
