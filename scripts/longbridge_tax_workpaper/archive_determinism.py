from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_COPY_BUFFER_SIZE = 64 * 1024


def write_deterministic_zip(
    output_path: str | Path,
    source_root: str | Path,
    *,
    archive_root_name: str | None = None,
) -> Path:
    """Write a byte-stable ZIP while streaming file contents.

    File order, timestamps, permissions, and compression settings are fixed.
    No source file is loaded wholly into memory, which keeps large PDF workpaper
    packages safe in constrained environments.
    """

    output_path = Path(output_path)
    source_root = Path(source_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = archive_root_name or source_root.name
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for path in files:
            arcname = f"{prefix}/{path.relative_to(source_root).as_posix()}"
            info = zipfile.ZipInfo(arcname, _FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            with path.open("rb") as source, target.open(info, "w", force_zip64=True) as destination:
                shutil.copyfileobj(source, destination, length=_COPY_BUFFER_SIZE)
    return output_path
