from __future__ import annotations

from pathlib import Path

FORBIDDEN_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "build",
    "dist",
    "outputs",
    "review_run_outputs",
    "htmlcov",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_NAMES = {".DS_Store", ".coverage"}


def forbidden_release_paths(root: str | Path) -> list[Path]:
    base = Path(root)
    problems: list[Path] = []
    for path in base.rglob("*"):
        relative_parts = path.relative_to(base).parts
        if ".git" in relative_parts:
            continue
        if (
            path.name in FORBIDDEN_NAMES
            or path.name in FORBIDDEN_DIRS
            or path.name.endswith(".egg-info")
            or path.suffix in FORBIDDEN_SUFFIXES
        ):
            problems.append(path)
    return sorted(problems, key=lambda item: item.as_posix())
