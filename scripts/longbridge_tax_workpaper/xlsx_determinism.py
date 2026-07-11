from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ID_RE = re.compile(r'Id="([^"]+)"')
_COPY_BUFFER_SIZE = 64 * 1024


def _decode_xml(data: bytes) -> tuple[str, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    return data.decode("utf-8-sig"), bom


def _encode_xml(text: str, bom: bool) -> bytes:
    payload = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if bom else payload


def _normalized_xml_payloads(source: zipfile.ZipFile) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    workbook_rels_name = "xl/_rels/workbook.xml.rels"
    workbook_name = "xl/workbook.xml"
    if workbook_rels_name in source.namelist() and workbook_name in source.namelist():
        rels_text, rels_bom = _decode_xml(source.read(workbook_rels_name))
        old_ids = _ID_RE.findall(rels_text)
        mapping = {old: f"rId{index}" for index, old in enumerate(old_ids, start=1)}
        for old, new in mapping.items():
            rels_text = rels_text.replace(f'Id="{old}"', f'Id="{new}"')
        payloads[workbook_rels_name] = _encode_xml(rels_text, rels_bom)

        workbook_text, workbook_bom = _decode_xml(source.read(workbook_name))
        for old, new in mapping.items():
            workbook_text = workbook_text.replace(f'r:id="{old}"', f'r:id="{new}"')
        payloads[workbook_name] = _encode_xml(workbook_text, workbook_bom)

    core_name = "docProps/core.xml"
    if core_name in source.namelist():
        core_text, core_bom = _decode_xml(source.read(core_name))
        fixed_time = "2000-01-01T00:00:00Z"
        core_text = re.sub(r"(<dcterms:created[^>]*>).*?(</dcterms:created>)", rf"\g<1>{fixed_time}\g<2>", core_text)
        core_text = re.sub(r"(<dcterms:modified[^>]*>).*?(</dcterms:modified>)", rf"\g<1>{fixed_time}\g<2>", core_text)
        payloads[core_name] = _encode_xml(core_text, core_bom)

    root_rels_name = "_rels/.rels"
    if root_rels_name in source.namelist():
        root_text, root_bom = _decode_xml(source.read(root_rels_name))
        root_ids = _ID_RE.findall(root_text)
        for index, old in enumerate(root_ids, start=1):
            root_text = root_text.replace(f'Id="{old}"', f'Id="rId{index}"')
        payloads[root_rels_name] = _encode_xml(root_text, root_bom)
    return payloads


def canonicalize_xlsx_package(path: str | Path) -> Path:
    """Normalize volatile XLSX metadata without buffering the entire workbook."""

    path = Path(path)
    temp_path: Path | None = None
    with zipfile.ZipFile(path, "r") as source:
        replacements = _normalized_xml_payloads(source)
        names = sorted(name for name in source.namelist() if not name.endswith("/"))
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temp:
            temp_path = Path(temp.name)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name in names:
                info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                if name in replacements:
                    target.writestr(info, replacements[name])
                else:
                    with source.open(name, "r") as input_stream, target.open(info, "w", force_zip64=True) as output_stream:
                        shutil.copyfileobj(input_stream, output_stream, length=_COPY_BUFFER_SIZE)

    # Windows forbids replacing a file while another process still has it
    # open.  Keep the atomic replacement outside the source ZipFile context.
    try:
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path
