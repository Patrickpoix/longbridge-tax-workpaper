from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

_DEFAULT_CHUNK_SIZE = 64 * 1024


def sha256_file(path: str | Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Return a SHA-256 digest without loading the file into memory.

    The implementation intentionally avoids ``Path.read_bytes`` and the
    ``iter(lambda: file.read(...), b"")`` pattern.  A fixed-size ``os.read``
    loop is reliable in constrained runtimes and makes memory use independent
    of the PDF, workbook, or ZIP size.
    """

    file_path = Path(path)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    file_stat = file_path.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"not a regular file: {file_path}")

    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(file_path, flags)
    try:
        while True:
            chunk = os.read(fd, chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()
