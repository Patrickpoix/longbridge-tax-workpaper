from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SourceType = Literal["native", "paddle", "azure", "derived", "manual", "missing"]
BBox = tuple[float, float, float, float] | None


@dataclass(slots=True)
class FieldValue:
    value: Any
    source: SourceType
    confidence: float
    raw_text: str | None = None
    page: int | None = None
    bbox: BBox = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def native(
        cls,
        value: Any,
        *,
        raw_text: str | None = None,
        page: int | None = None,
        bbox: BBox = None,
        confidence: float = 0.9,
        warnings: list[str] | None = None,
    ) -> "FieldValue":
        return cls(
            value=value,
            source="native",
            confidence=confidence,
            raw_text=raw_text,
            page=page,
            bbox=bbox,
            warnings=list(warnings or []),
        )

    @classmethod
    def derived(
        cls,
        value: Any,
        *,
        raw_text: str | None = None,
        page: int | None = None,
        bbox: BBox = None,
        confidence: float = 0.85,
        warnings: list[str] | None = None,
    ) -> "FieldValue":
        return cls(
            value=value,
            source="derived",
            confidence=confidence,
            raw_text=raw_text,
            page=page,
            bbox=bbox,
            warnings=list(warnings or []),
        )

    @classmethod
    def missing(cls, *, raw_text: str | None = None, warnings: list[str] | None = None) -> "FieldValue":
        return cls(
            value=None,
            source="missing",
            confidence=1.0,
            raw_text=raw_text,
            page=None,
            bbox=None,
            warnings=list(warnings or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox else None,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class SectionResult:
    name: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    rows: list[dict[str, FieldValue]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "rows": [
                {key: value.to_dict() for key, value in row.items()}
                for row in self.rows
            ],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ValidationResult:
    rule: str
    passed: bool
    message: str
    severity: Literal["info", "warning", "error"] = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class StatementResult:
    statement_month: str
    source_pdf: str
    account: dict[str, FieldValue] = field(default_factory=dict)
    metadata: dict[str, FieldValue] = field(default_factory=dict)
    sections: dict[str, SectionResult] = field(default_factory=dict)
    validations: list[ValidationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_month": self.statement_month,
            "source_pdf": str(Path(self.source_pdf)),
            "account": {key: value.to_dict() for key, value in self.account.items()},
            "metadata": {key: value.to_dict() for key, value in self.metadata.items()},
            "sections": {key: value.to_dict() for key, value in self.sections.items()},
            "validations": [validation.to_dict() for validation in self.validations],
            "warnings": list(self.warnings),
        }
