from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

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
    "pdf_extracts",
    "runtime_config",
}
FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pdf",
    ".xlsx",
    ".xls",
    ".csv",
    ".zip",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}
FORBIDDEN_NAMES = {".DS_Store", ".coverage", ".env"}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".txt",
    ".cfg",
    ".ini",
    ".bat",
    ".ps1",
}

# Public fixtures use this deliberately synthetic sentinel.
PUBLIC_SYNTHETIC_ACCOUNT_IDS = {"H00000001"}
PUBLIC_SYNTHETIC_SECRET_VALUES = {"test-password"}
ACCOUNT_LIKE_RE = re.compile(r"\bH\d{8}\b")
SECRET_ASSIGNMENT_RE = re.compile(
    r'''(?ix)
    \b(api[_-]?key|access[_-]?token|secret|password)\b
    \s*[:=]\s*
    ["']([^"'\r\n]{8,})["']
    '''
)


def _private_blocked_tokens() -> tuple[str, ...]:
    raw = os.environ.get("LONGBRIDGE_RELEASE_BLOCKED_TOKENS", "")
    return tuple(token.strip() for token in raw.splitlines() if token.strip())


def forbidden_release_paths(root: str | Path) -> list[Path]:
    base = Path(root)
    problems: list[Path] = []
    for path in base.rglob("*"):
        relative_parts = path.relative_to(base).parts
        if ".git" in relative_parts:
            continue
        lower_suffix = path.suffix.lower()
        if (
            path.name in FORBIDDEN_NAMES
            or path.name.startswith(".env.")
            or path.name in FORBIDDEN_DIRS
            or path.name.endswith(".egg-info")
            or lower_suffix in FORBIDDEN_SUFFIXES
        ):
            problems.append(path)
    return sorted(problems, key=lambda item: item.as_posix())


def sensitive_text_findings(
    root: str | Path,
    *,
    blocked_tokens: Iterable[str] | None = None,
) -> list[str]:
    base = Path(root)
    blocked = tuple(blocked_tokens) if blocked_tokens is not None else _private_blocked_tokens()
    findings: list[str] = []

    for path in base.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if ".git" in relative.parts:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in blocked:
            if token in text:
                findings.append(f"{relative.as_posix()}: contains private blocked token")
        for match in ACCOUNT_LIKE_RE.findall(text):
            if match not in PUBLIC_SYNTHETIC_ACCOUNT_IDS:
                findings.append(f"{relative.as_posix()}: contains account-like identifier")
        for secret_match in SECRET_ASSIGNMENT_RE.finditer(text):
            if secret_match.group(2) not in PUBLIC_SYNTHETIC_SECRET_VALUES:
                findings.append(f"{relative.as_posix()}: contains secret-like assignment")

    return sorted(set(findings))
