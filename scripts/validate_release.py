#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from longbridge_tax_workpaper.release_hygiene import (
    forbidden_release_paths,
    sensitive_text_findings,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a staged public Skill tree")
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = Path(args.root).resolve()

    required = [root / "SKILL.md", root / "agents" / "openai.yaml", root / "pyproject.toml"]
    problems = [
        f"missing required file: {path.relative_to(root).as_posix()}"
        for path in required
        if not path.is_file()
    ]
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
