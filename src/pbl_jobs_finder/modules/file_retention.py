"""Bounded cleanup for application-owned temporary files only."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

EXPORT_TTL_SECONDS = 86400
_MANAGED = re.compile(r"(?:\d+-[a-f0-9]{32}\.(?:pdf|docx)|[a-f0-9]{32}\.pdf|\.\d+-[a-f0-9]{32}\.[a-f0-9]{32}\.tmp\.pdf)")
logger = logging.getLogger(__name__)


def clean_expired_files(root: Path, *, now: float | None = None) -> int:
    """Never recurse or follow symlinks; preserve legacy and unknown files."""
    if not root.is_dir() or root.is_symlink():
        return 0
    cutoff = (time.time() if now is None else now) - EXPORT_TTL_SECONDS
    removed = 0
    for path in root.iterdir():
        if not _MANAGED.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            if path.is_file() and path.stat().st_mtime <= cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.warning("Temporary file cleanup deferred")
    return removed


def export_path(root: Path, key: str) -> tuple[int, Path] | None:
    match = re.fullmatch(r"([1-9]\d*)-[a-f0-9]{32}\.(pdf|docx)", key)
    if match is None:
        return None
    path = root / key
    if path.is_symlink() or path.resolve().parent != root.resolve():
        return None
    return int(match[1]), path
