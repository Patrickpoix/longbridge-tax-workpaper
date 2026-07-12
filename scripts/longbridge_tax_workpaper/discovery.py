from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .hashing import sha256_file
from .pipeline import parse_statement
from .schema import StatementResult



def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_pdfs(input_dir: str | Path, *, exclude_roots: Iterable[str | Path] = ()) -> list[Path]:
    root = Path(input_dir).resolve()
    excluded = [Path(item).resolve() for item in exclude_roots]
    candidates: list[Path] = []
    for path in sorted(root.rglob("*.pdf")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(_inside(path, ex) for ex in excluded):
            continue
        if any(part in {".git", "__pycache__", ".agents", ".vendor", ".pytest_cache"} for part in path.parts):
            continue
        if any(part.startswith("longbridge_") and (part.endswith("_workpapers") or part.endswith("_processed_delivery")) for part in path.parts):
            continue
        # Exclude common backup/temp directories that may contain PDF copies
        if any(
            part.startswith(prefix)
            for part in path.parts
            for prefix in ("audit_chatgpt_delivery_", "full_system_fix_", "skill-review", "outputs")
        ):
            continue
        candidates.append(path)
    unique: dict[str, Path] = {}
    for path in candidates:
        unique.setdefault(sha256_file(path), path)
    return sorted(unique.values(), key=lambda p: p.as_posix())


def parse_pdf_set(pdf_paths: Iterable[str | Path], *, password: str | None = None, enable_ocr: bool = True) -> list[StatementResult]:
    statements = [parse_statement(Path(path), password=password, enable_ocr=enable_ocr) for path in sorted((Path(p) for p in pdf_paths), key=lambda p: p.name)]
    return sorted(statements, key=lambda item: item.statement_month)


def split_account_and_year(
    statements: Iterable[StatementResult], *, tax_year: int | None = None, account_id: str | None = None,
) -> tuple[int, str | None, list[StatementResult], list[StatementResult]]:
    rows = list(statements)
    accounts: dict[str | None, list[StatementResult]] = defaultdict(list)
    for statement in rows:
        value = statement.account.get("account_id") or statement.metadata.get("account_id")
        accounts[value.value if value else None].append(statement)

    if account_id is not None:
        selected = accounts.get(account_id)
        if not selected:
            raise ValueError(f"未找到指定账户: {account_id}")
        selected_account = account_id
    elif len(accounts) == 1:
        selected_account, selected = next(iter(accounts.items()))
    else:
        visible = [key or "UNKNOWN" for key in accounts]
        raise ValueError(f"检测到多个账户，请用 --account-id 指定: {visible}")

    years: dict[int, list[StatementResult]] = defaultdict(list)
    seen_months: set[str] = set()
    for statement in selected:
        month = str(statement.statement_month)
        if month in seen_months:
            raise ValueError(f"同一账户存在重复月结单月份: {month}")
        seen_months.add(month)
        if len(month) == 6 and month.isdigit() and 1 <= int(month[4:]) <= 12:
            years[int(month[:4])].append(statement)
    if tax_year is None:
        complete = []
        for year, items in years.items():
            actual = {item.statement_month for item in items}
            expected = {f"{year}{month:02d}" for month in range(1, 13)}
            if actual == expected:
                complete.append(year)
        if complete:
            tax_year = max(complete)
        elif years:
            coverage = {year: sorted(item.statement_month for item in items) for year, items in years.items()}
            raise ValueError(
                "未指定纳税年度，且没有任何年份包含完整1月至12月月结单；"
                f"请补齐年度或显式使用 --tax-year 生成不完整年度工作底稿。coverage={coverage}"
            )
        else:
            raise ValueError("无法从PDF识别纳税年度")

    primary = sorted(years.get(int(tax_year), []), key=lambda item: item.statement_month)
    prior = sorted([item for year, items in years.items() if year < int(tax_year) for item in items], key=lambda item: item.statement_month)
    if not primary:
        raise ValueError(f"未找到{tax_year}年月结单")
    return int(tax_year), selected_account, primary, prior
