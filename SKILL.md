---
name: longbridge-tax-workpaper
description: generate reviewable chinese tax workpapers from encrypted or unencrypted longbridge securities monthly statement pdfs for one account and one tax year. use only for longbridge monthly statement pdf to china tax workpaper conversion, including trade parsing, opening-cost reconstruction, fifo and moving-average realized pnl, dividends and withholding, margin-interest audit schedules, one chinese multi-sheet excel, a separate workpaper archive, and review-readiness checks.
---

# Longbridge Tax Workpaper

Generate a reviewable workpaper, never claim to create a legally final tax return.

## Workflow

1. Locate the uploaded Longbridge monthly statement PDFs for one account.
2. Use a PDF password already supplied in the conversation. Otherwise ask once. Pass it only through `LONGBRIDGE_PDF_PASSWORD`; never place it in CLI arguments, source, logs, workbooks, manifests, or reports.
3. Determine the requested tax year. If absent, let the program select only the latest year containing exactly January through December. Do not silently treat a partial year as complete.
4. Obtain the December 31 USD/CNY and HKD/CNY middle rates from an official Chinese source when possible. Pass the rates and source metadata to the program. If unavailable, omit the rates; keep CNY outputs blank and mark them incomplete.

### Quick install & interactive mode (no args needed)

```bash
python -m pip install .
longbridge-tax-workpaper   # ← 无参数时自动进入交互式引导
```

### Full CLI mode (with args)

```bash
export LONGBRIDGE_PDF_PASSWORD='<runtime-password>'
longbridge-tax-workpaper <pdf-directory> \
  --output-dir <output-directory> \
  --tax-year <year> \
  --fx USD=<rate> \
  --fx HKD=<rate> \
  --fx-source USD=<official-url> \
  --fx-source HKD=<official-url> \
  --fx-source-date USD=<YYYY-12-31> \
  --fx-source-date HKD=<YYYY-12-31> \
  --cost-basis-method MOVING_AVERAGE \
  --withholding-credit \
  --deduct-margin-interest
```

### Windows one-click

Double-click `start.bat` — it handles venv creation, pip install, and interactive prompts.

6. Verify that the output contains one multi-sheet Excel workbook, one workpaper ZIP, one processed-delivery ZIP, and one review-status JSON.
7. Inspect at least `年度纳税汇总`, `财产转让计税情景`, `FIFO已实现盈亏`, `移动平均已实现盈亏`, `股息与预扣税`, `期初逐月持仓对账`, `持仓数量对账`, and `复核就绪性`.
8. Return links to:
   - `longbridge_<year>_processed_results.xlsx`
   - `longbridge_<year>_workpapers.zip`
   - `longbridge_<year>_processed_delivery.zip`
   - `review_status_<year>.json`

## Required behavior

- Keep all processed spreadsheet reports in one Excel workbook with Chinese-named sheets.
- Keep JSON, CSV, configuration, hashes, and evidence in a separate workpaper ZIP. Exclude source PDFs by default; include them only when the user explicitly requests local archival and warn that they are highly sensitive.
- Include only realized P&L in annual trading results; exclude unrealized P&L.
- Produce FIFO and moving weighted average results.
- Produce separate-market netting, same-account cross-market netting, and positive-disposals-without-loss-offset scenarios.
- Never present a scenario as the sole legally confirmed filing method without archived authority evidence.
- Show statement withholding as a tax-credit candidate. Keep unconditional automatic credit at zero unless qualifying evidence supports it.
- Show financing-interest accrual and actual-payment bases separately. Do not deduct financing interest by default.
- If prior statements are missing, still generate the workbook when possible and mark opening-cost evidence incomplete or blocked.
- Match statement layouts by normalized capability anchors and known header aliases, not by hard-coded calendar year.
- Use the native PDF text layer first. If text is degraded or template recognition fails, use the optional OCR fallback; retain provenance and require validation before accepting OCR-assisted structure.
- Reject layouts still unknown after the controlled fallback with `unknown_template`; never silently fall back to a legacy parser.
- Escalate any month recognized only via OCR fallback or with a low template-signature score to `REVIEW_REQUIRED` through the `月结单版式识别置信度` readiness check; never trust a degraded layout silently.
- Reuse text extraction caches only when both cached filename and SHA-256 match the exact PDF.
- Exclude output directories from input discovery and deduplicate PDFs by SHA-256.
- Treat `TECHNICALLY_GENERATED`, `REVIEW_REQUIRED`, and `BLOCKED_FOR_REVIEW` as review states only; none means ready to file.

## References

Read `references/tax-boundaries.md` before presenting tax conclusions.
Read `references/output-sheets.md` when checking workbook completeness.
Read `references/precision-and-evidence.md` when reviewing rates, rounding, source files, or caches.
Read `references/troubleshooting.md` when parsing fails or a new PDF layout appears.
