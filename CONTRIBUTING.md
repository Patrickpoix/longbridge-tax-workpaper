# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]" -c constraints.txt
python -m pytest -q
```

## Requirements for changes

- Add a regression test for every parser, cost-basis, currency, tax-summary, or packaging change.
- Keep unknown statement layouts fail-closed.
- Do not add real account identifiers, names, passwords, PDFs, or generated workbooks.
- Do not infer a security mapping from a partial name. Add an exact, auditable mapping record instead.
- Preserve both FIFO and moving-average outputs and keep disputed tax treatments labeled as scenarios.
- Run `python scripts/validate_release.py <clean-staged-tree>` before packaging.

## New statement layouts

Document a minimum layout signature, add synthetic or anonymized fixtures, and prove that an unrelated PDF remains `unknown_template`.
