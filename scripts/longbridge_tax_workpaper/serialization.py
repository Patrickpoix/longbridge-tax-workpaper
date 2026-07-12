from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .schema import FieldValue, SectionResult, StatementResult


def field_value(value: FieldValue | None):
    return value.value if value else None


def write_statement_json(statement: StatementResult, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(statement.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def section_rows(statements: Iterable[StatementResult], section_name: str) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for statement in statements:
        section = statement.sections.get(section_name, SectionResult(name=section_name))
        for index, row in enumerate(section.rows, start=1):
            record = {
                "statement_month": statement.statement_month,
                "source_pdf": Path(statement.source_pdf).name,
                "row_index": index,
            }
            record.update({key: field_value(value) for key, value in row.items()})
            output.append(record)
    return output


def write_csv(path: str | Path, rows: list[dict[str, object]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Use dict.fromkeys to collect unique headers in insertion order (O(rows × 1))
    seen: dict[str, None] = {}
    for row in rows:
        seen.update(dict.fromkeys(row))
    headers = list(seen)
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return target
