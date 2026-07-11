from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...ingest import IngestedDocument, PageData
from ...normalize import normalize_text, parse_amount
from ...schema import FieldValue, SectionResult

FIELD_ORDER = [
    "cash_balance",
    "market_value",
    "total_assets",
    "financing_amount",
    "financing_limit",
    "initial_margin_requirement",
    "maintenance_margin_requirement",
    "currency_margin_requirement",
    "short_settlement_margin",
    "margin_call",
    "equity_with_loan_value",
]
INLINE_LIMIT_FIELDS = FIELD_ORDER[:10]
NO_LIMIT_FIELDS = [
    "cash_balance",
    "market_value",
    "total_assets",
    "financing_amount",
    "initial_margin_requirement",
    "maintenance_margin_requirement",
    "currency_margin_requirement",
    "short_settlement_margin",
    "margin_call",
]
BORROWED_EQUITY_FIELDS = [
    "cash_balance",
    "market_value",
    "total_assets",
    "financing_amount",
    "initial_margin_requirement",
    "maintenance_margin_requirement",
    "short_settlement_margin",
    "margin_call",
    "equity_with_loan_value",
]

LABEL_MAP = {
    "\u8d44\u91d1\u4f59\u989d": "cash_balance",
    "\u5e02\u503c": "market_value",
    "\u603b\u8d44\u4ea7": "total_assets",
    "\u878d\u8d44\u91d1\u989d": "financing_amount",
    "\u878d\u8d44\u9650\u989d": "financing_limit",
    "\u521d\u59cb\u4fdd\u8bc1\u91d1\u8981\u6c42": "initial_margin_requirement",
    "\u7ef4\u6301\u4fdd\u8bc1\u91d1\u8981\u6c42": "maintenance_margin_requirement",
    "\u8d27\u5e01\u4fdd\u8bc1\u91d1\u8981\u6c42": "currency_margin_requirement",
    "\u878d\u5238\u5e73\u4ed3\u62c5\u4fdd\u91d1": "short_settlement_margin",
    "\u5e94\u8ffd\u7f34\u4fdd\u8bc1\u91d1": "margin_call",
    "\u542b\u8d37\u6743\u76ca\u4ef7\u503c": "equity_with_loan_value",
}
ACCOUNT_OVERVIEW = "\u8d26\u6237\u603b\u89c8"
FINANCING_LIMIT = "\u878d\u8d44\u9650\u989d"
NUMBER_CHARS = set("0123456789,.-")


@dataclass(slots=True)
class WordGroup:
    top: float
    words: list[dict[str, Any]]

    @property
    def text(self) -> str:
        return " ".join(str(word["text"]) for word in self.words)


def _bbox_for_words(words: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    if not words:
        return None
    return (
        min(float(word["x0"]) for word in words),
        min(float(word["top"]) for word in words),
        max(float(word["x1"]) for word in words),
        max(float(word["bottom"]) for word in words),
    )


def _group_words(page: PageData, *, tolerance: float = 4.0) -> list[WordGroup]:
    words = sorted(page.words, key=lambda word: (float(word["top"]), float(word["x0"])))
    groups: list[WordGroup] = []
    for word in words:
        top = float(word["top"])
        if not groups or abs(groups[-1].top - top) > tolerance:
            groups.append(WordGroup(top=top, words=[word]))
        else:
            groups[-1].words.append(word)
    for group in groups:
        group.words.sort(key=lambda word: float(word["x0"]))
    return groups


def _is_number_word(word: dict[str, Any]) -> bool:
    text = normalize_text(word.get("text", ""))
    if not text or any(char not in NUMBER_CHARS for char in text):
        return False
    try:
        parse_amount(text)
    except ValueError:
        return False
    return any(char.isdigit() for char in text)


def _normalized_label(text: str) -> str:
    return normalize_text(text, compact=True).rstrip(":")


def _group_fields(group: WordGroup) -> list[str]:
    fields: list[str] = []
    for word in group.words:
        label = _normalized_label(str(word["text"]))
        field_name = LABEL_MAP.get(label)
        if field_name:
            fields.append(field_name)
    return fields


def _number_words(group: WordGroup) -> list[dict[str, Any]]:
    return [word for word in group.words if _is_number_word(word)]


def _extract_header_financing_limit(page: PageData, groups: list[WordGroup]) -> FieldValue:
    for group in groups:
        if group.top > 155:
            continue
        words = group.words
        for index, word in enumerate(words):
            if FINANCING_LIMIT not in _normalized_label(str(word["text"])):
                continue
            for candidate in words[index + 1 :]:
                if _is_number_word(candidate):
                    return FieldValue.native(
                        parse_amount(candidate["text"]),
                        raw_text=str(candidate.get("raw_text", candidate["text"])),
                        page=page.page_number,
                        bbox=_bbox_for_words([candidate]),
                        confidence=0.93,
                    )
    return FieldValue.missing(warnings=["Header financing limit not present"])


def _find_overview_groups(groups: list[WordGroup]) -> tuple[WordGroup | None, WordGroup | None]:
    anchor_index: int | None = None
    for index, group in enumerate(groups):
        if ACCOUNT_OVERVIEW in _normalized_label(group.text):
            anchor_index = index
            break

    if anchor_index is not None:
        header_group = None
        value_group = None
        for group in groups[anchor_index + 1 : anchor_index + 5]:
            if len(_group_fields(group)) >= 5:
                header_group = group
                continue
            if len(_number_words(group)) >= 8:
                value_group = group
                break
        return header_group, value_group

    numeric_groups = [group for group in groups if 8 <= len(_number_words(group)) <= 11]
    if not numeric_groups:
        return None, None
    return None, numeric_groups[0]


def _field_names_for_values(header_group: WordGroup | None, values: list[dict[str, Any]]) -> tuple[list[str], str, float]:
    header_fields = _group_fields(header_group) if header_group else []
    if header_fields and len(header_fields) == len(values):
        return header_fields, "header_mapped", 0.95
    if len(values) == 10:
        return list(INLINE_LIMIT_FIELDS), "legacy_inline_financing_limit", 0.88
    if len(values) == 9 and header_fields:
        return header_fields[:9], "header_mapped_partial", 0.92
    if len(values) == 9:
        return list(NO_LIMIT_FIELDS), "legacy_no_financing_limit", 0.86
    return [], "unrecognized", 0.0


def extract_account_overview(document: IngestedDocument) -> tuple[SectionResult, FieldValue]:
    page = document.pages[0]
    groups = _group_words(page)
    header_financing_limit = _extract_header_financing_limit(page, groups)
    header_group, value_group = _find_overview_groups(groups)
    section = SectionResult(name="account_overview")
    section.fields = {field_name: FieldValue.missing() for field_name in FIELD_ORDER}

    if value_group is None:
        section.warnings.append("Account overview values not located by native parser")
        return section, header_financing_limit

    value_words = _number_words(value_group)
    field_names, source_mode, confidence = _field_names_for_values(header_group, value_words)
    if not field_names:
        section.warnings.append(f"Account overview structure not recognized: {len(value_words)} values")
        return section, header_financing_limit

    for field_name, word in zip(field_names, value_words):
        section.fields[field_name] = FieldValue.native(
            parse_amount(word["text"]),
            raw_text=str(word.get("raw_text", word["text"])),
            page=page.page_number,
            bbox=_bbox_for_words([word]),
            confidence=confidence,
            warnings=[source_mode],
        )
    return section, header_financing_limit
