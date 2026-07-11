"""PaddleOCR fallback extractor.

OCR is deliberately lazy: importing PaddleOCR at module import time can start
background runtime state and make ordinary parser/test runs slow or hang on
process shutdown.  We only import it when OCR is explicitly requested.
"""

from __future__ import annotations

import importlib.util
import logging
import tempfile
from pathlib import Path
from typing import Any

from ...schema import FieldValue, SectionResult

logger = logging.getLogger(__name__)


def _paddle_available() -> bool:
    return importlib.util.find_spec("paddleocr") is not None


def _render_page_image(document: Any, page_number: int, output_dir: Path) -> Path | None:
    try:
        import pdfplumber

        pdf_path = document.path
        with pdfplumber.open(str(pdf_path), password=getattr(document, "password", None)) as pdf:
            if page_number > len(pdf.pages):
                return None
            page = pdf.pages[page_number - 1]
            img = page.to_image(resolution=300)
            output = output_dir / f"page-{page_number}.png"
            img.save(str(output))
            return output
    except Exception as exc:
        logger.warning("OCR render failed for page %s: %s", page_number, exc)
        return None


def _run_paddle_ocr(image_path: Path) -> str:
    if not _paddle_available():
        return ""
    import paddleocr  # type: ignore

    try:
        # PaddleOCR 3.x pipeline API.
        ocr = paddleocr.PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
        )
        predictions = ocr.predict(input=str(image_path))
        lines: list[str] = []
        for prediction in predictions:
            payload = prediction.json if hasattr(prediction, "json") else prediction
            if callable(payload):
                payload = payload()
            if isinstance(payload, dict):
                result = payload.get("res", payload)
                lines.extend(str(text) for text in result.get("rec_texts", []) if text)
        return "\n".join(lines)
    except (AttributeError, TypeError):
        # PaddleOCR 2.x compatibility path.
        ocr = paddleocr.PaddleOCR(lang="ch", use_angle_cls=True)
        result = ocr.ocr(str(image_path))
        if not result or not result[0]:
            return ""
        return "\n".join(
            str(line[1][0])
            for line in result[0]
            if len(line) > 1 and line[1][0]
        )


def _ocr_page_text(document: Any, page_number: int) -> str:
    if not _paddle_available():
        return ""
    with tempfile.TemporaryDirectory(prefix="longbridge-ocr-") as temp_dir:
        image_path = _render_page_image(document, page_number, Path(temp_dir))
        if image_path is None:
            return ""
        try:
            return _run_paddle_ocr(image_path)
        except Exception as exc:
            logger.warning("PaddleOCR page %s failed: %s", page_number, exc)
            return ""


def extract_document_ocr_text(
    document: Any,
    page_numbers: set[int] | None = None,
) -> dict[int, str]:
    """OCR selected pages and return non-empty text without persistent images."""

    if not _paddle_available():
        return {}
    selected = page_numbers or {page.page_number for page in document.pages}
    return {
        page_number: text
        for page_number in sorted(selected)
        if (text := _ocr_page_text(document, page_number))
    }


def extract_section_ocr(
    document: Any,
    page_number: int,
    section_name: str,
    reason: str = "native_missing",
) -> SectionResult:
    text = _ocr_page_text(document, page_number)
    section = SectionResult(name=section_name)
    section.fields["ocr_available"] = FieldValue.derived(_paddle_available(), confidence=1.0)
    if not text:
        section.warnings.append(f"OCR fallback produced no text for page {page_number} (reason: {reason})")
        return section

    section.fields["ocr_raw_text"] = FieldValue(
        value=text,
        source="paddle",
        confidence=0.7,
        raw_text=text[:500],
        page=page_number,
        warnings=[f"OCR fallback triggered: {reason}"],
    )
    return section
