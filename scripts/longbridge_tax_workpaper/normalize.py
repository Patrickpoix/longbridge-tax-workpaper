from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_CHAR_REPLACEMENTS = str.maketrans(
    {
        # Traditional -> simplified Chinese used by some statement templates.
        "資": "资",
        "額": "额",
        "戶": "户",
        "現": "现",
        "紅": "红",
        "貸": "贷",
        "貨": "货",
        "維": "维",
        "權": "权",
        "價": "价",
        "動": "动",
        "費": "费",
        "發": "发",
        "幣": "币",
        "類": "类",
        "備": "备",
        "註": "注",
        "匯": "汇",
        "總": "总",
        "證": "证",
        "賬": "账",
        "關": "关",
        "勵": "励",
        "請": "请",
        "長": "长",
        "黃": "黄",
        # Kangxi / CJK radical glyphs observed in pdfplumber output.
        "⾦": "金", "⼊": "入", "⽣": "生", "⽇": "日", "⽉": "月",
        "⼾": "户", "⼝": "口", "⾏": "行", "⽤": "用", "⽬": "目",
        "⾹": "香", "⾼": "高", "⻛": "风", "⽔": "水", "⽽": "而",
        "⽜": "牛", "⾮": "非", "⾃": "自", "⾜": "足", "⽀": "支",
        "⼈": "人", "⽅": "方", "⽂": "文", "⼿": "手", "⼦": "子",
        "⼆": "二", "⼀": "一", "⼄": "乙", "⼗": "十", "⼋": "八",
        "⼭": "山", "⽐": "比", "⽌": "止", "⻩": "黄", "⻓": "长",
        "⾞": "车", "⻔": "门", "⾥": "里", "⽯": "石", "⼠": "士",
        "⽴": "立", "⼤": "大", "⼩": "小", "⽺": "羊", "⻄": "西",
    }
)

_PHRASE_REPLACEMENTS = {
    "\u878d\u8cc7": "\u878d\u8d44",
    "\u73fe\u91d1": "\u73b0\u91d1",
    "\u7dad\u6301": "\u7ef4\u6301",
    "\u542b\u8cb8": "\u542b\u8d37",
    "\u8cc7\u91d1": "\u8d44\u91d1",
    "\u5165\u8cec": "\u5165\u8d26",
    "\u5165\u8cec": "\u5165\u8d26",
    "\u8cc7\u91d1\u5165\u8cec": "\u8d44\u91d1\u5165\u8d26",
    "\u8cc7\u91d1\u5165\u8d26": "\u8d44\u91d1\u5165\u8d26",
    "\u73fe\u91d1\u5956\u52f5": "\u73b0\u91d1\u5956\u52b1",
    "\u8acb": "\u8bf7",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f]")
_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"[-]?\s*\d[\d,]*(?:\.\d+)?")


@dataclass(slots=True)
class AmountCandidate:
    text: str
    value: float
    start: int
    end: int
    score: float
    reasons: list[str] = field(default_factory=list)


def normalize_text(text: object, *, compact: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.translate(_CHAR_REPLACEMENTS)
    for old, new in _PHRASE_REPLACEMENTS.items():
        normalized = normalized.replace(old, new)
    normalized = _CONTROL_RE.sub(" ", normalized)
    normalized = normalized.replace("\u2212", "-").replace("\uff0d", "-")
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    if compact:
        normalized = normalized.replace(" ", "")
    return normalized


def parse_amount(text: object) -> float:
    value = normalize_text(text)
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative = True
        value = value[1:-1]
    value = value.replace(" ", "").replace(",", "")
    if value.startswith("-"):
        negative = True
        value = value[1:]
    parsed = float(value)
    return -parsed if negative else parsed


def _candidate_from_match(match: re.Match[str], *, score: float, reason: str) -> AmountCandidate:
    token = match.group(1) if match.lastindex else match.group(0)
    # If the capture is inside the regex, derive best-effort offsets from the token.
    start = match.start(1) if match.lastindex else match.start(0)
    end = match.end(1) if match.lastindex else match.end(0)
    return AmountCandidate(token.strip(), parse_amount(token), start, end, score, [reason])


def _pick_cash_dividend_amount(text: str) -> AmountCandidate | None:
    """Pick the actual cash amount for a dividend row.

    Dividend detail rows often contain several numeric values: per-share amount,
    withholding percentage, held quantity, and actual cash paid.  This parser
    treats `/SH` / `per Share` values as metadata and favors the cash amount
    next to a comma, `PAY IN`, or after a `Held:` quantity.
    """
    # PAY IN 1,134.32 (actual cash paid).  Skip PAY IN HKD0.24543/SH per-share rates.
    pay_in_cash = re.search(
        r"PAY IN\s+(?:HKD|USD|RMB|CNY)?\s*([-]?\d[\d,]*\.\d+)(?!\s*(?:/SH|PER\s+SHARE))",
        text,
        re.IGNORECASE,
    )
    if pay_in_cash:
        # Reject if the number is immediately followed by /SH after a currency prefix.
        after = text[pay_in_cash.end(1): pay_in_cash.end(1) + 16].upper()
        if "/SH" not in after and "PER SHARE" not in after:
            return _candidate_from_match(pay_in_cash, score=120.0, reason="dividend_pay_in_cash")

    # Common shape: "..., 490.86 PAY IN HKD0.24543/SH" or ", 9.21 Held:30".
    before_pay_in = list(
        re.finditer(
            r",\s*([-]?\d[\d,]*\.\d+)\s+(?=(?:PAY IN|Held:|HELD:|$))",
            text,
            re.IGNORECASE,
        )
    )
    if before_pay_in:
        return _candidate_from_match(before_pay_in[-1], score=115.0, reason="dividend_cash_before_pay_in_or_held")

    # Common shape: "Held:1 2.21".
    after_held = re.search(r"Held:\s*\d[\d,]*(?:\.\d+)?\s+([-]?\d[\d,]*\.\d+)", text, re.IGNORECASE)
    if after_held:
        return _candidate_from_match(after_held, score=112.0, reason="dividend_cash_after_held")

    # HK dividends can have percentage text around the cash amount: "(- 218.55 10%)".
    pct_wrapped = re.search(r"\(-\s*([-]?\d[\d,]*\.\d+)\s*\d+(?:\.\d+)?%\)", text)
    if pct_wrapped:
        return _candidate_from_match(pct_wrapped, score=110.0, reason="dividend_cash_inside_tax_parentheses")

    return None




def _pick_company_action_cash_amount(text: str) -> AmountCandidate | None:
    """Pick cash amount for company-action cash settlement rows.

    Rows can contain warrant identifiers, expiry day counts, reference prices, and
    payout amounts.  The cash amount appears before the reference-price prose
    (for example: ``467.00 AVG CLOSING PR ... HKD64.05``).
    """
    before_avg = re.search(r"([-]?\d[\d,]*\.\d+)\s+(?=AVG\b|AVERAGE\b|SETTLEMENT\b)", text, re.IGNORECASE)
    if before_avg:
        return _candidate_from_match(before_avg, score=125.0, reason="company_action_cash_before_reference_price")

    payout = re.search(r"PAYOUT[^-\d]*([-]?\d[\d,]*\.\d+)", text, re.IGNORECASE)
    if payout:
        return _candidate_from_match(payout, score=118.0, reason="company_action_payout_amount")

    # Fallback: choose the first decimal amount after the security/action prose.
    candidates = [m for m in re.finditer(r"[-]?\d[\d,]*\.\d+", text)]
    if candidates:
        return _candidate_from_match(candidates[0], score=90.0, reason="company_action_cash_first_decimal")
    return None


def find_amount_candidates(raw_text: str, *, transaction_type: str = "") -> list[AmountCandidate]:
    text = normalize_text(raw_text)
    candidates: list[AmountCandidate] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        if not any(char.isdigit() for char in token):
            continue
        try:
            value = parse_amount(token)
        except ValueError:
            continue

        start, end = match.span()
        before = text[max(0, start - 35) : start]
        after = text[end : min(len(text), end + 35)]
        context = f"{before}{token}{after}"
        score = 0.0
        reasons: list[str] = []

        if "." in token or "," in token:
            score += 10
            reasons.append("decimal_or_grouped")
        else:
            score -= 15
            reasons.append("integer_downrank")
        if token.strip().startswith("-"):
            score += 8
            reasons.append("signed_amount")
        # PAY IN near a per-share rate is not a cash amount.  Examples:
        # "PAY IN HKD0.24543/SH(NET)".
        if "PAY IN" in before:
            if "/SH" in after.upper()[:12] or "PER SHARE" in after.upper()[:20]:
                score -= 75
                reasons.append("pay_in_per_share_rate")
            else:
                score += 70
                reasons.append("pay_in")
        if re.search(r"\(-\s*$", before) and re.search(r"^\s*\d+%", after):
            score += 65
            reasons.append("gross_dividend_before_tax_rate")
        if re.search(r"Held:\s*$", before, re.IGNORECASE):
            score -= 80
            reasons.append("held_quantity")
        if "APPROX." in before.upper() or "APPROX." in context.upper():
            score -= 90
            reasons.append("approximate_fx")
        if "/SH" in after.upper() or "PER SHARE" in after.upper():
            score -= 35
            reasons.append("per_share_value")
        if "/SH" in before.upper() or "PER SHARE" in before.upper():
            score -= 20
            reasons.append("near_per_share_value")
        if "%" in after[:8] and "gross_dividend_before_tax_rate" not in reasons:
            score -= 20
            reasons.append("percentage_context")
        if "利息" in transaction_type and token.strip().startswith("-"):
            score += 35
            reasons.append("interest_signed")
        if "公司行动其他费用" in transaction_type and token.strip().startswith("-"):
            score += 35
            reasons.append("company_action_fee_signed")
        if transaction_type == "ADR收费" and token.strip().startswith("-"):
            score += 35
            reasons.append("adr_fee_signed")
        if "现金分红" in transaction_type and value > 0:
            score += 15
            reasons.append("positive_dividend_cash")
        if "资金入账" in transaction_type and value > 0:
            score += 35
            reasons.append("company_action_cash_in")
        if "活动礼包" in transaction_type and value > 0:
            score += 35
            reasons.append("cash_reward")

        candidates.append(
            AmountCandidate(
                text=token.strip(),
                value=value,
                start=start,
                end=end,
                score=score,
                reasons=reasons,
            )
        )
    return candidates


def pick_amount(raw_text: str, *, transaction_type: str = "") -> AmountCandidate | None:
    text = normalize_text(raw_text)
    normalized_type = normalize_text(transaction_type)

    if "现金分红" in normalized_type:
        dividend_candidate = _pick_cash_dividend_amount(text)
        if dividend_candidate is not None:
            return dividend_candidate

    if "公司行动资金入账" in normalized_type or "公司行动资金出账" in normalized_type:
        company_action_cash = _pick_company_action_cash_amount(text)
        if company_action_cash is not None:
            return company_action_cash

    pay_in = re.search(r"PAY IN\s+([-]?\s*\d[\d,]*\.\d+)(?!\s*(?:/SH|PER\s+SHARE))", text, re.IGNORECASE)
    if pay_in:
        token = pay_in.group(1)
        return AmountCandidate(token, parse_amount(token), pay_in.start(1), pay_in.end(1), 100.0, ["pay_in_direct"])

    gross = re.search(r"\(-\s*(\d[\d,]*\.\d+)\s*\d+%\)", text)
    if gross and "现金分红" in normalized_type:
        token = gross.group(1)
        return AmountCandidate(token, parse_amount(token), gross.start(1), gross.end(1), 95.0, ["gross_dividend_direct"])

    candidates = find_amount_candidates(text, transaction_type=normalized_type)
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.score, candidate.start))


def canonical_transaction_type(raw_type: str, raw_detail: str = "") -> str:
    # Check the explicit raw_type label first to avoid misclassification from
    # broad text matches across entries (e.g. ADR Fee detail capturing nearby
    # "融资利息" text).
    normalized_type = normalize_text(raw_type)
    if "ADR" in normalized_type:
        return "adr_fee"
    if "公司行动股票进账" in normalized_type:
        return "company_action_stock_in"
    if "公司行动股票出账" in normalized_type or "强制性企业行动股票出账" in normalized_type:
        return "company_action_stock_out"
    if "公司行动资金入账" in normalized_type:
        return "company_action_cash_in"
    if "活动礼包" in normalized_type:
        return "cash_reward"
    if "公司行动" in normalized_type:
        return "company_action_fee"
    if "股票交易" in normalized_type:
        return "stock_trade_cash_flow"
    if "现金分红" in normalized_type:
        return "cash_dividend"
    if "存入资金" in normalized_type:
        return "deposit"
    if "提出资金" in normalized_type:
        return "withdrawal"

    # For types not explicitly labeled (e.g. legacy "贷款利息"), fall back to
    # combined text matching.
    text = normalize_text(f"{raw_type} {raw_detail}")
    if "利息" in text or "贷款" in text:
        return "margin_interest"
    if "现金分红" in text:
        return "cash_dividend"
    if "存入资金" in text:
        return "deposit"
    if "提出资金" in text:
        return "withdrawal"
    if "公司行动资金入账" in text:
        return "company_action_cash_in"
    if "活动礼包" in text:
        return "cash_reward"
    if "公司行动股票出账" in text or "强制性企业行动股票出账" in text:
        return "company_action_stock_out"
    if "公司行动" in text:
        return "company_action_fee"
    if "ADR" in text:
        return "adr_fee"
    if "股票交易" in text:
        return "stock_trade_cash_flow"
    return "other"


def classify_tax_category(transaction_type: str, raw_detail: str) -> str:
    detail = normalize_text(raw_detail).lower()
    if transaction_type == "cash_dividend":
        return "dividend_income"
    if transaction_type == "margin_interest":
        return "margin_interest_deductible"
    if transaction_type in {"deposit", "withdrawal"}:
        return "non_taxable_cash_movement"
    if transaction_type in {"company_action_stock_in", "company_action_stock_out"}:
        return "non_cash_company_action"
    if transaction_type == "company_action_cash_in":
        # Company-action cash can be a dividend, redemption, derivative
        # settlement or another event.  Keep it unresolved until the full
        # same-date/security context is inspected by the fund-flow extractor.
        return "company_action_cash_pending_review"
    if transaction_type == "cash_reward":
        return "cash_reward_other_income"
    if transaction_type == "company_action_fee":
        if "tax" in detail:
            return "withholding_tax"
        if "handling fee" in detail or "scrip fee" in detail:
            return "service_fee_deductible"
        return "company_action_fee_non_deductible"
    if transaction_type == "adr_fee":
        return "service_fee_deductible"
    if transaction_type == "stock_trade_cash_flow":
        return "trading_related"
    return "pending_review"
