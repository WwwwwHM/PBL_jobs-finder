"""Local OCR fallback for image-only resume PDFs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Protocol

import pypdfium2 as pdfium

MAX_OCR_PAGES = 5
OCR_RENDER_SCALE = 1.0


class ResumeOCRError(RuntimeError):
    """An image PDF could not be converted into usable resume text."""


class OCREngine(Protocol):
    def __call__(self, image: object) -> tuple[list[list[object]] | None, object]: ...


_OCR_LOCK = Lock()


@lru_cache(maxsize=1)
def _get_ocr_engine() -> OCREngine:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(use_angle_cls=False)


def extract_text_from_image_pdf(
    file_path: str | Path,
    *,
    engine: OCREngine | None = None,
) -> str:
    """Render an image PDF and recognize its text without external services."""

    try:
        document = pdfium.PdfDocument(str(file_path))
    except Exception as exc:
        raise ResumeOCRError("图片型 PDF 无法打开，请改用文本粘贴方式") from exc

    try:
        page_count = len(document)
        if page_count == 0:
            return ""
        if page_count > MAX_OCR_PAGES:
            raise ResumeOCRError(
                f"图片型 PDF 最多支持 {MAX_OCR_PAGES} 页，请精简后重试"
            )

        try:
            ocr_engine = engine or _get_ocr_engine()
        except Exception as exc:
            raise ResumeOCRError(
                "本地 OCR 组件加载失败，请联系管理员或改用文本粘贴方式"
            ) from exc
        page_texts: list[str] = []
        for page_index in range(page_count):
            page = document[page_index]
            try:
                image = page.render(scale=OCR_RENDER_SCALE).to_pil()
                with _OCR_LOCK:
                    result, _ = ocr_engine(image)
            except Exception as exc:
                raise ResumeOCRError("图片文字识别失败，请改用文本粘贴方式") from exc
            finally:
                page.close()

            lines = [
                str(item[1]).strip()
                for item in (result or [])
                if len(item) >= 2 and str(item[1]).strip()
            ]
            if lines:
                page_texts.append("\n".join(lines))
        return "\n\n".join(page_texts).strip()
    finally:
        document.close()


__all__ = [
    "MAX_OCR_PAGES",
    "OCR_RENDER_SCALE",
    "ResumeOCRError",
    "extract_text_from_image_pdf",
]
