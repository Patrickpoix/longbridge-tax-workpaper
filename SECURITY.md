# Security and privacy

This project processes brokerage statements containing account identifiers, balances, holdings, and transaction history.

- Never commit real statement PDFs, generated workbooks, runtime configuration, passwords, or `.env` files.
- Supply PDF passwords only through `LONGBRIDGE_PDF_PASSWORD` for the current process.
- Source PDFs are excluded from the workpaper ZIP by default. Use `--include-source-pdfs` only for local archival.
- Share `processed_delivery.zip`, not a PDF-containing workpaper archive, with external reviewers unless disclosure is intentional.
- Use synthetic or irreversibly anonymized fixtures for public tests.

Report security or privacy issues privately to the repository maintainer before opening a public issue.
