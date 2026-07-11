from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...ingest import IngestedDocument, PageData
from ...normalize import normalize_text, parse_amount
from ...schema import FieldValue, SectionResult

CASH_BALANCE_FIELDS = [
    "opening_balance",
    "change_amount",
    "ending_balance",
    "settled_cash",
    "unsettled_cash",
    "accrued_interest",
    "reference_rate",
    "ending_hkd_balance",
]
HOLDING_FIELDS = [
    "opening_position",
    "quantity_change",
    "ending_position",
    "price",
    "market_value",
    "cost",
    "unrealized_pnl",
    "maintenance_margin_ratio",
    "maintenance_margin",
]
DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
HK_HOLDING_RE = re.compile(r"^\d{3,5}\s")
US_TICKER_RE = re.compile(r"^[A-Z]{1,6}(?:\s|$)")
MISSING_TOKENS = {"/", "N/A"}
INVESTMENT_DETAIL = "投资组合详情"
SUMMARY_LABEL = "汇总"
CURRENCY_LABELS = {"港元", "美元", "汇总", "汇总(HKD)", "汇总(港元)", "汇总(美元)"}
USD_NAME_MARKERS = {
    "ETF",
    "Direxion",
    "ProShares",
    "Daily",
    "Shares",
    "Trust",
    "Fund",
    "拼多多",
    "博通",
    "联合健康",
    "Vistra",
    "百度",
    "蔚来",
    "Oscar",
    "Cleveland",
    "Leverage Shares",
    "富时中国",
    "航空航天",
    "黄金",
    "倍做多",
}
HKD_NAME_MARKERS = {
    "农业银行",
    "中国宏桥",
    "华泰证券",
    "万洲国际",
    "中国再保险",
    "协鑫科技",
    "博时中国创业板",
    "广发",
    "中芯",
    "中核",
    "珍酒",
    "石油",
    "三生制药",
}
VALUE_TOKEN_RE = re.compile(r"(?:N/A|/|-?\d[\d,]*(?:\.\d+)?%?)")
MARKET_HEADER_RE = re.compile(r"^(股票|期权)\s*\(([^;()]+);\s*(港元|美元)\)")
TRADE_SECTION_MARKERS = {"股票交易明细", "期权交易明细", "其他资金出入明细", "责任说明"}
PAGE_HEADER_RE = re.compile(r"^\d{4}\.\d{2}(?:\.\d{2})?$")


@dataclass(slots=True)
class WordCluster:
    words: list[dict[str, Any]]

    @property
    def top(self) -> float:
        return min(float(word["top"]) for word in self.words)

    @property
    def ordered_words(self) -> list[dict[str, Any]]:
        return sorted(self.words, key=lambda word: (float(word["top"]), float(word["x0"])))

    @property
    def text(self) -> str:
        return " ".join(str(word["text"]) for word in self.ordered_words)


@dataclass(slots=True)
class HoldingContext:
    asset_type: str | None = None
    market: str | None = None
    currency: str | None = None
    started: bool = False
    stop: bool = False


def _bbox_for_words(words: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    if not words:
        return None
    return (
        min(float(word["x0"]) for word in words),
        min(float(word["top"]) for word in words),
        max(float(word["x1"]) for word in words),
        max(float(word["bottom"]) for word in words),
    )


def _cluster_words(page: PageData, *, gap: float = 16.0) -> list[WordCluster]:
    words = sorted(page.words, key=lambda word: (float(word["top"]), float(word["x0"])))
    if not words:
        return []

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_top: float | None = None
    for word in words:
        top = float(word["top"])
        if previous_top is not None and top - previous_top > gap:
            if current:
                clusters.append(current)
            current = []
        current.append(word)
        previous_top = top
    if current:
        clusters.append(current)
    return [WordCluster(words=cluster) for cluster in clusters]


def _join_label(tokens: list[str]) -> str:
    label = " ".join(token for token in tokens if token).strip()
    return re.sub(r"\s+\(", "(", label)


def _parse_token_value(token: str) -> tuple[float | None, str]:
    normalized = normalize_text(token)
    if normalized in MISSING_TOKENS:
        return None, normalized
    if normalized.endswith("%"):
        return parse_amount(normalized[:-1]) / 100.0, normalized
    return parse_amount(normalized), normalized


def _is_value_like(token: str) -> bool:
    normalized = normalize_text(token)
    if normalized in MISSING_TOKENS:
        return True
    if normalized.endswith("%"):
        normalized = normalized[:-1]
    try:
        parse_amount(normalized)
    except ValueError:
        return False
    return any(char.isdigit() for char in normalized)


def _field_from_word(word: dict[str, Any], *, page_number: int, confidence: float = 0.92) -> FieldValue:
    value, raw_text = _parse_token_value(str(word["text"]))
    return FieldValue.native(
        value,
        raw_text=raw_text,
        page=page_number,
        bbox=_bbox_for_words([word]),
        confidence=confidence,
    )


def _infer_holding_currency(label: str, context_currency: str | None = None) -> tuple[str | None, str, float]:
    if context_currency in {"HKD", "USD"}:
        return context_currency, "market_group_header", 0.99

    normalized = normalize_text(label)
    if HK_HOLDING_RE.match(normalized):
        return "HKD", "hk_numeric_code", 0.97

    first_token = normalized.split()[0] if normalized.split() else ""
    if US_TICKER_RE.match(first_token):
        return "USD", "us_ticker_prefix", 0.95

    if any(marker in normalized for marker in HKD_NAME_MARKERS):
        return "HKD", "hk_name_marker", 0.9
    if any(marker in normalized for marker in USD_NAME_MARKERS):
        return "USD", "us_name_marker", 0.88

    return None, "currency_not_found_in_holding_label", 0.0


def _extract_cash_balances(page: PageData, clusters: list[WordCluster]) -> SectionResult:
    rows: list[dict[str, FieldValue]] = []
    holding_anchor = next((cluster.top for cluster in clusters if INVESTMENT_DETAIL in normalize_text(cluster.text, compact=True)), None)
    cash_limit_top = holding_anchor if holding_anchor is not None else 320.0

    for cluster in clusters:
        if cluster.top >= cash_limit_top:
            continue
        ordered = cluster.ordered_words
        label_words = [word for word in ordered if float(word["x0"]) < 90]
        data_words = [word for word in ordered if float(word["x0"]) >= 90 and _is_value_like(str(word["text"]))]
        if not label_words or len(data_words) < len(CASH_BALANCE_FIELDS):
            continue

        label = _join_label([str(word["text"]) for word in label_words])
        if not any(marker in label for marker in CURRENCY_LABELS):
            continue

        row = {
            "currency_label": FieldValue.native(
                label,
                raw_text=label,
                page=page.page_number,
                bbox=_bbox_for_words(label_words),
                confidence=0.95,
            )
        }
        for field_name, word in zip(CASH_BALANCE_FIELDS, data_words[: len(CASH_BALANCE_FIELDS)]):
            row[field_name] = _field_from_word(word, page_number=page.page_number, confidence=0.93)
        rows.append(row)

    section = SectionResult(name="cash_balances", rows=rows)
    section.fields["row_count"] = FieldValue.derived(len(rows), page=page.page_number, confidence=0.95)
    if not rows:
        section.warnings.append("Cash balance rows not located on page 1")
    return section


def _currency_code_from_cn(label: str | None) -> str | None:
    if label == "港元":
        return "HKD"
    if label == "美元":
        return "USD"
    return None


def _line_is_noise(line: str) -> bool:
    compact = normalize_text(line, compact=True)
    if not compact:
        return True
    if PAGE_HEADER_RE.match(compact):
        return True
    if "综合账户月结单" in compact or "账户总览" in compact or "资金详情" in compact:
        return True
    if "项目期初持仓变更数量" in compact:
        return True
    if "Page" in line and "of" in line:
        return True
    # Generic account-holder/address header detection. Never hard-code a real person's name.
    if any(marker in compact for marker in ("地址", "邮箱", "电邮", "联系电话", "客户姓名", "账户名称")):
        return True
    # Footer/header lines commonly contain a short Chinese name followed by an account id and page counter.
    if re.match(r"^[\u4e00-\u9fff]{2,8}[A-Z]\d{6,}Page\d+of\d+$", compact, re.IGNORECASE):
        return True
    return False


def _parse_holding_line(line: str, context: HoldingContext, page_number: int) -> dict[str, FieldValue] | None:
    normalized = normalize_text(line)
    matches = list(VALUE_TOKEN_RE.finditer(normalized))
    if len(matches) < len(HOLDING_FIELDS):
        return None

    value_matches = matches[-len(HOLDING_FIELDS):]
    label = normalized[: value_matches[0].start()].strip()
    suffix = normalized[value_matches[-1].end() :].strip()
    label = re.sub(r"\s+", " ", label)
    if suffix and not DATE_RE.match(suffix.split()[0]) and not any(marker in suffix for marker in TRADE_SECTION_MARKERS):
        label = re.sub(r"\s+", " ", f"{label} {suffix}").strip()
    if not label or label.startswith(SUMMARY_LABEL) or DATE_RE.match(label.split()[0]):
        return None
    if label in {"股票", "期权"} or any(marker in label for marker in TRADE_SECTION_MARKERS):
        return None
    # Cash-balance rows can leave leading numeric/currency fragments before the
    # last nine numbers and otherwise look like holding rows. Security labels may
    # start with HK numeric codes, but they should not start with a signed amount
    # or contain standalone currency labels.
    if re.match(r"^-?\d", label) and not HK_HOLDING_RE.match(label) and "倍" not in label:
        return None
    if any(currency_label in label for currency_label in ("港元", "美元")) and not HK_HOLDING_RE.match(label):
        return None
    if not re.search(r"[A-Za-z一-鿿]", label):
        return None

    currency, reason, confidence = _infer_holding_currency(label, context.currency)
    row: dict[str, FieldValue] = {
        "asset_type": FieldValue.derived(context.asset_type or "unknown", raw_text=line, page=page_number, confidence=0.95 if context.asset_type else 0.5),
        "market": FieldValue.derived(context.market, raw_text=line, page=page_number, confidence=0.95 if context.market else 0.5),
        "name": FieldValue.native(label, raw_text=label, page=page_number, confidence=0.94),
        "currency": (
            FieldValue.derived(currency, raw_text=label, page=page_number, confidence=confidence, warnings=[reason])
            if currency is not None
            else FieldValue.missing(raw_text=label, warnings=[reason])
        ),
    }
    for field_name, match in zip(HOLDING_FIELDS, value_matches):
        raw = match.group(0)
        try:
            value, raw_text = _parse_token_value(raw)
        except ValueError:
            value, raw_text = None, normalize_text(raw)
        row[field_name] = FieldValue.native(value, raw_text=raw_text, page=page_number, confidence=0.9)
    return row


def _append_holding_name_continuation(row: dict[str, FieldValue], line: str) -> None:
    continuation = normalize_text(line)
    if not continuation:
        return
    name = str(row["name"].value or "").strip()
    # Do not append obvious unrelated footers/headers.
    if any(marker in continuation for marker in TRADE_SECTION_MARKERS) or continuation.startswith(SUMMARY_LABEL):
        return
    combined = f"{name} {continuation}".replace(" - ", " - ")
    combined = re.sub(r"\s+", " ", combined).strip()
    row["name"].value = combined
    row["name"].raw_text = combined
    # Re-infer currency after continuation adds US ETF markers.
    current_currency = row.get("currency", FieldValue.missing()).value
    if current_currency is None:
        currency, reason, confidence = _infer_holding_currency(combined)
        if currency:
            row["currency"] = FieldValue.derived(currency, raw_text=combined, confidence=confidence, warnings=[reason])


def _extract_holdings_from_text(document: IngestedDocument) -> SectionResult:
    rows: list[dict[str, FieldValue]] = []
    context = HoldingContext()
    last_row: dict[str, FieldValue] | None = None

    for page in document.pages:
        if context.stop:
            break
        for raw_line in page.text.splitlines():
            line = normalize_text(raw_line)
            if not line:
                continue
            if any(marker in line for marker in TRADE_SECTION_MARKERS):
                if "股票交易明细" in line or "期权交易明细" in line or "其他资金出入明细" in line:
                    context.stop = True
                    break
            if INVESTMENT_DETAIL in line:
                context.started = True
                continue

            if _line_is_noise(line):
                continue

            # Legacy one-page layouts do not print the investment-detail anchor
            # or market group headers.  They still have parseable holding rows
            # after cash-balance rows.  We therefore allow the row parser to
            # discover the first holding line, while stopping when dated trade /
            # fund-flow lines begin.  Page header dates were already skipped by
            # _line_is_noise above.
            first_token = line.split()[0] if line.split() else ""
            if context.started and DATE_RE.match(first_token):
                context.stop = True
                break

            market_match = MARKET_HEADER_RE.search(line)
            if market_match:
                context.asset_type = market_match.group(1)
                context.market = market_match.group(2).replace("市场", "市场")
                context.currency = _currency_code_from_cn(market_match.group(3))
                last_row = None
                continue

            if line.startswith(SUMMARY_LABEL):
                last_row = None
                continue

            row = _parse_holding_line(line, context, page.page_number)
            if row is not None:
                context.started = True
                rows.append(row)
                last_row = row
                continue

            if last_row is not None:
                _append_holding_name_continuation(last_row, line)

    section = SectionResult(name="holdings", rows=rows)
    section.fields["row_count"] = FieldValue.derived(len(rows), confidence=0.95)
    if not rows:
        section.warnings.append("Holding rows not located by text parser")
    return section


def _extract_legacy_holdings_from_page1_clusters(document: IngestedDocument) -> SectionResult:
    """Parse old compact templates whose page text omits the investment header.

    2025-01..2025-06 layouts list cash balances, then holding rows, then
    transaction/fund-flow rows on page 1.  The words are still visually clustered
    by row, so this fallback reads those clusters instead of relying on the
    missing section anchor.
    """
    if not document.pages:
        return SectionResult(name="holdings")
    rows: list[dict[str, FieldValue]] = []
    page = document.pages[0]
    context = HoldingContext(started=True)
    last_row: dict[str, FieldValue] | None = None
    for cluster in _cluster_words(page):
        if cluster.top < 300:
            continue
        line = normalize_text(cluster.text)
        if not line or _line_is_noise(line):
            continue
        if DATE_RE.match(line.split()[0]) or any(marker in line for marker in TRADE_SECTION_MARKERS):
            break
        row = _parse_holding_line(line, context, page.page_number)
        if row is not None:
            rows.append(row)
            last_row = row
            continue
        if last_row is not None:
            _append_holding_name_continuation(last_row, line)
    section = SectionResult(name="holdings", rows=rows)
    section.fields["row_count"] = FieldValue.derived(len(rows), confidence=0.88, warnings=["legacy_cluster_fallback"])
    if not rows:
        section.warnings.append("Legacy holding rows not located by cluster parser")
    return section




def extract_portfolio_sections(document: IngestedDocument) -> tuple[SectionResult, SectionResult]:
    page = document.pages[0]
    clusters = _cluster_words(page)
    cash_balances = _extract_cash_balances(page, clusters)
    holdings = _extract_holdings_from_text(document)
    if not holdings.rows:
        holdings = _extract_legacy_holdings_from_page1_clusters(document)
    return cash_balances, holdings
