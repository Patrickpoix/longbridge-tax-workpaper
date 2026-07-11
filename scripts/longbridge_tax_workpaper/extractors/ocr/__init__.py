"""OCR fallback extraction layer."""

from .paddle import extract_document_ocr_text, extract_section_ocr

__all__ = ["extract_document_ocr_text", "extract_section_ocr"]
