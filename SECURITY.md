# Security and privacy

This project processes brokerage statements containing account identifiers, balances, holdings, transaction history, and tax-workpaper outputs.

## Data handling

- Never commit real statement PDFs, generated workbooks, runtime configuration, passwords, `.env*` files, private keys, or real account identifiers.
- Interactive password entry is non-echoing. For non-interactive runs, use `LONGBRIDGE_PDF_PASSWORD` only in the controlled current process. Never pass passwords as CLI arguments or persist them in source, logs, manifests, workbooks, or reports.
- Public tests and examples must use synthetic or irreversibly anonymized data. Do not hide real sensitive values in source through string fragments, encodings, concatenation, or other reversible obfuscation.
- `.gitignore` reduces accidental local staging; it is not a release-control or history-sanitization mechanism. `scripts/validate_release.py` and the CI release-tree scan are the release gates.

## Delivery privacy levels

1. `workpapers.zip` — highest sensitivity. It contains detailed audit material and may include original PDFs when `--include-source-pdfs` is explicitly enabled.
2. `processed_delivery.zip` — still sensitive. It excludes original PDFs but retains account, transaction, holding, and source-file traceability information. Share only with explicitly authorized professional reviewers.
3. `sanitized_delivery.zip` — preferred when deidentified aggregate review is sufficient. It removes account numbers, transaction/holding detail, source filenames/hashes, and detailed readiness explanations. Aggregate financial amounts remain sensitive.

If sensitive data has already entered published Git history, deleting it in the current tree is insufficient; a separately gated history rewrite is required after source remediation, validation, backup, and identity checks.

Report security or privacy issues privately to the repository maintainer before opening a public issue.
