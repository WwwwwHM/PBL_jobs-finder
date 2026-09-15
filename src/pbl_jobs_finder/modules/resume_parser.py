"""Validation and text extraction for uploaded PDF resumes."""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
from PyPDF2 import PdfReader

MAX_PDF_SIZE = 10 * 1024 * 1024
MAX_RESUME_CHARACTERS = 60_000


class ResumeParseError(ValueError):
    """An uploaded resume cannot be safely used for diagnosis."""


def parse_resume_pdf(file_path: str | Path) -> str:
    """Extract normalized text with a second parser when PyPDF2 falls short."""

    path = Path(file_path)
    if not path.is_file():
        raise ResumeParseError("找不到上传的简历文件，请重新上传")
    if path.suffix.lower() != ".pdf":
        raise ResumeParseError("仅支持 PDF 格式的简历")
    if path.stat().st_size > MAX_PDF_SIZE:
        raise ResumeParseError("PDF 文件不能超过 10 MB")

    extraction_errors: list[Exception] = []
    completed_extraction = False
    try:
        text = _extract_with_pypdf(path)
        completed_extraction = True
    except ResumeParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize third-party parser failures
        extraction_errors.append(exc)
        text = ""

    if not text:
        try:
            text = _extract_with_pdfplumber(path)
            completed_extraction = True
        except Exception as exc:  # noqa: BLE001 - normalize parser fallback failures
            extraction_errors.append(exc)

    if not completed_extraction:
        cause = extraction_errors[-1] if extraction_errors else None
        raise ResumeParseError("PDF 解析失败，请改用文本粘贴方式") from cause

    text = _normalize_extracted_text(text)
    if not text and _pdf_contains_images(path):
        from pbl_jobs_finder.modules.resume_ocr import (
            ResumeOCRError,
            extract_text_from_image_pdf,
        )

        try:
            text = _normalize_extracted_text(extract_text_from_image_pdf(path))
        except ResumeOCRError as exc:
            raise ResumeParseError(str(exc)) from exc
    if not text:
        raise ResumeParseError("PDF 中未识别到文字，请改用文本粘贴方式")
    if len(text) > MAX_RESUME_CHARACTERS:
        raise ResumeParseError("简历内容过长，请精简到 60000 字以内")
    return text


def _extract_with_pypdf(path: Path) -> str:
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ResumeParseError("暂不支持加密 PDF，请解除密码后重新上传")
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page for page in pages if page.strip())


def _extract_with_pdfplumber(path: Path) -> str:
    with pdfplumber.open(path) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    return "\n\n".join(page for page in pages if page.strip())


def _normalize_extracted_text(value: str) -> str:
    lines = []
    for raw_line in value.replace("\x00", "").replace("\u00a0", " ").splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _pdf_contains_images(path: Path) -> bool:
    try:
        reader = PdfReader(str(path), strict=False)
        return any(page.images for page in reader.pages)
    except Exception:  # noqa: BLE001 - extraction already supplied the user-facing error
        return False


__all__ = ["MAX_PDF_SIZE", "MAX_RESUME_CHARACTERS", "ResumeParseError", "parse_resume_pdf"]
