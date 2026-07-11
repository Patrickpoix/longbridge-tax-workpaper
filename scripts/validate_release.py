#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from longbridge_tax_workpaper.release_hygiene import forbidden_release_paths

# Construct sensitive tokens from fragments so this validator does not contain
# the complete private values it is designed to reject.
BLOCKED_TEXT = {
    "artifact" + "_tool",
    "黄" + "品" + "杰",
    "H108" + "04580",
    "3231" + "4831",
}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini"}


def sensitive_text_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in BLOCKED_TEXT:
            if token in text:
                findings.append(f"{path.relative_to(root).as_posix()}: contains blocked token")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a staged public Skill tree")
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = Path(args.root).resolve()

    required = [root / "SKILL.md", root / "agents" / "openai.yaml", root / "pyproject.toml"]
    problems = [f"missing required file: {path.relative_to(root).as_posix()}" for path in required if not path.is_file()]
    problems.extend(path.relative_to(root).as_posix() for path in forbidden_release_paths(root))
    problems.extend(sensitive_text_findings(root))
    if problems:
        for item in problems:
            print(item)
        return 1
    print("RELEASE_TREE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
