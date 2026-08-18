"""Filesystem helpers shared by the publishing modules."""

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write data to path so readers never observe a partial file.

    Writes to a temporary file in the same directory, then renames it over the
    target. os.replace is atomic within a filesystem, so a device downloading
    the previous version keeps reading it until the rename completes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            # mkstemp creates 0600 files and the rename preserves that mode,
            # which locks the web server out of everything published for it.
            os.fchmod(f.fileno(), 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
