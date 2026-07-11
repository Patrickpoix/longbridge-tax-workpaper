from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .normalize import normalize_text


@dataclass(slots=True)
class PageData:
    page_number: int
    width: float
    height: float
    text: str
    normalized_text: str
    words: list[dict[str, Any]]


@dataclass(slots=True)
class IngestedDocument:
    path: Path
    pages: list[PageData]
    password: str | None = None
    rendered_images: list[Path] = field(default_factory=list)
    ingest_source: str = "pdfplumber"

    @property
    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def normalized_full_text(self) -> str:
        return "\n".join(page.normalized_text for page in self.pages)


_MONTH_RE = re.compile(r"statement-monthly-(20\d{2})(\d{2})-")



def _sidecar_dir(pdf_path: Path) -> Path | None:
    match = _MONTH_RE.search(pdf_path.name)
    if not match:
        return None
    folder_name = f"{match.group(1)}-{match.group(2)}"
    candidates = [pdf_path.parent / "pdf_extracts" / folder_name, Path.cwd() / "pdf_extracts" / folder_name]
    source_hash = sha256_file(pdf_path)
    for candidate in candidates:
        manifest_path = candidate / "manifest.json"
        if not candidate.exists() or not (candidate / "page_001_text.txt").exists() or not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("source_pdf_sha256") != source_hash:
            continue
        if manifest.get("source_pdf_name") not in (None, pdf_path.name):
            continue
        return candidate
    return None


def _load_pdf_sidecar(pdf_path: Path, password: str | None) -> IngestedDocument | None:
    if os.environ.get("LONGBRIDGE_DISABLE_PDF_EXTRACT_CACHE") == "1":
        return None
    folder = _sidecar_dir(pdf_path)
    if folder is None:
        return None
    pages: list[PageData] = []
    for text_path in sorted(folder.glob("page_*_text.txt")):
        page_num = int(text_path.stem.split("_")[1])
        raw_page_text = text_path.read_text(encoding="utf-8")
        words_path = folder / f"page_{page_num:03d}_words.json"
        words: list[dict[str, Any]] = []
        if page_num == 1 and words_path.exists():
            for word in json.loads(words_path.read_text(encoding="utf-8")):
                raw_text = str(word.get("text", ""))
                normalized_word = normalize_text(raw_text)
                if normalized_word:
                    words.append({**word, "raw_text": raw_text, "text": normalized_word})
        pages.append(PageData(page_num, 595.0, 842.0, raw_page_text, normalize_text(raw_page_text), words))
    if not pages:
        return None
    return IngestedDocument(path=pdf_path, pages=pages, password=password, ingest_source="pdf_extracts_verified")


def _load_pdf_direct(path: str | Path, password: str | None = None) -> IngestedDocument:
    import pdfplumber

    pdf_path = Path(path).resolve()
    pages: list[PageData] = []
    try:
        pdf_context = pdfplumber.open(str(pdf_path), password=password)
    except Exception as exc:
        message = str(exc).strip()
        hint = "PDF密码错误或缺失" if not message else f"无法打开PDF: {message}"
        raise ValueError(f"{pdf_path.name}: {hint}") from exc
    with pdf_context as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            words: list[dict[str, Any]] = []
            if index == 1:
                for word in page.extract_words() or []:
                    raw_text = str(word.get("text", ""))
                    normalized_word = normalize_text(raw_text)
                    if normalized_word:
                        words.append({**word, "raw_text": raw_text, "text": normalized_word})
            raw_page_text = page.extract_text() or ""
            pages.append(PageData(index, float(page.width), float(page.height), raw_page_text, normalize_text(raw_page_text), words))
            flush_cache = getattr(page, "flush_cache", None)
            if callable(flush_cache):
                flush_cache()
    return IngestedDocument(path=pdf_path, pages=pages, password=password, ingest_source="pdfplumber")


def load_pdf(path: str | Path, *, password: str | None = None) -> IngestedDocument:
    pdf_path = Path(path).resolve()
    sidecar = _load_pdf_sidecar(pdf_path, password)
    if sidecar is not None:
        return sidecar
    return _load_pdf_direct(pdf_path, password)
