"""Private local upload storage behind a replaceable object-store interface."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pbl_jobs_finder.exceptions import ResumeParseError

MAX_PDF_SIZE = 12 * 1024 * 1024
_KEY = re.compile(r"[a-f0-9]{32}\.pdf")
logger = logging.getLogger(__name__)


def validate_pdf_upload(source: str | Path) -> Path:
    path = Path(source)
    try:
        if path.is_symlink() or not path.is_file():
            raise ResumeParseError("找不到上传的简历文件，请重新上传")
        if path.suffix.lower() != ".pdf":
            raise ResumeParseError("仅支持 PDF 格式的简历")
        size = path.stat().st_size
        if size == 0:
            raise ResumeParseError("PDF 文件为空，请重新上传")
        if size > MAX_PDF_SIZE:
            raise ResumeParseError("PDF 文件不能超过 12 MB")
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise ResumeParseError("PDF 解析失败：文件格式无效，请重新上传或粘贴文本")
    except OSError as exc:
        raise ResumeParseError("简历文件读取失败，请重新上传") from exc
    return path


class FileStorage(Protocol):
    def save_pdf(self, source: str | Path) -> str: ...
    def materialize(self, key: str) -> AbstractContextManager[Path]: ...
    def delete(self, key: str) -> None: ...


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ResumeParseError("无效的文件标识")
        path = self.root / key
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ResumeParseError("无效的文件路径")
        return path

    def save_pdf(self, source: str | Path) -> str:
        source_path = validate_pdf_upload(source)
        self.root.mkdir(parents=True, exist_ok=True)
        key = f"{uuid4().hex}.pdf"
        destination = self._path(key)
        try:
            with source_path.open("rb") as reader, destination.open("xb") as writer:
                size = 0
                while chunk := reader.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_PDF_SIZE:
                        raise ResumeParseError("PDF 文件不能超过 12 MB")
                    writer.write(chunk)
            validate_pdf_upload(destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return key

    @contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        yield validate_pdf_upload(self._path(key))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


@contextmanager
def staged_pdf(storage: FileStorage, source: str | Path) -> Iterator[Path]:
    key = storage.save_pdf(source)
    try:
        with storage.materialize(key) as local_path:
            yield local_path
    finally:
        try:
            storage.delete(key)
        except OSError:
            # A cleanup failure must not hide the original parsing failure.
            logger.exception("Unable to clean staged upload key=%s", key)
