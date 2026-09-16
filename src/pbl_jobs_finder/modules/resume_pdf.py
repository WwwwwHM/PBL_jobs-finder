"""Validated resume JSON, safe HTML rendering, and Playwright PDF export."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, Field(max_length=160)]
LongText = Annotated[str, Field(max_length=1200)]
Highlight = Annotated[str, Field(max_length=600)]

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 40_000_000
MAX_RENDERED_PHOTO_SIZE = (1200, 1600)
_DEFAULT_AVATAR_PATH = Path(__file__).with_name("assets") / "default_avatar.svg"

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class ResumeDocumentError(ValueError):
    """The model response cannot be represented by the resume schema."""


class ResumePDFError(RuntimeError):
    """The validated resume could not be rendered as a PDF."""


class _ResumeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResumeBasics(_ResumeModel):
    name: ShortText = "个人简历"
    headline: ShortText = ""
    phone: ShortText = ""
    email: ShortText = ""
    location: ShortText = ""
    website: ShortText = ""
    summary: LongText = ""


class ResumeExperience(_ResumeModel):
    company: ShortText = ""
    role: ShortText = ""
    start_date: ShortText = ""
    end_date: ShortText = ""
    highlights: list[Highlight] = Field(default_factory=list, max_length=20)

    @field_validator("highlights")
    @classmethod
    def normalize_highlights(cls, value: list[str]) -> list[str]:
        return _normalize_list(value)


class ResumeProject(_ResumeModel):
    name: ShortText = ""
    role: ShortText = ""
    start_date: ShortText = ""
    end_date: ShortText = ""
    highlights: list[Highlight] = Field(default_factory=list, max_length=20)

    @field_validator("highlights")
    @classmethod
    def normalize_highlights(cls, value: list[str]) -> list[str]:
        return _normalize_list(value)


class ResumeEducation(_ResumeModel):
    school: ShortText = ""
    degree: ShortText = ""
    major: ShortText = ""
    start_date: ShortText = ""
    end_date: ShortText = ""
    highlights: list[Highlight] = Field(default_factory=list, max_length=10)

    @field_validator("highlights")
    @classmethod
    def normalize_highlights(cls, value: list[str]) -> list[str]:
        return _normalize_list(value)


class ResumeAdditionalSection(_ResumeModel):
    title: ShortText
    highlights: list[Highlight] = Field(default_factory=list, max_length=20)

    @field_validator("highlights")
    @classmethod
    def normalize_highlights(cls, value: list[str]) -> list[str]:
        return _normalize_list(value)


class ResumeDocument(_ResumeModel):
    basics: ResumeBasics = Field(default_factory=ResumeBasics)
    skills: list[ShortText] = Field(default_factory=list, max_length=50)
    experience: list[ResumeExperience] = Field(default_factory=list, max_length=30)
    projects: list[ResumeProject] = Field(default_factory=list, max_length=30)
    education: list[ResumeEducation] = Field(default_factory=list, max_length=20)
    certificates: list[ShortText] = Field(default_factory=list, max_length=30)
    additional_sections: list[ResumeAdditionalSection] = Field(
        default_factory=list, max_length=10
    )

    @field_validator("skills", "certificates")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        return _normalize_list(value)

    @model_validator(mode="after")
    def require_resume_content(self) -> ResumeDocument:
        has_content = any(
            (
                self.basics.headline,
                self.basics.phone,
                self.basics.email,
                self.basics.location,
                self.basics.website,
                self.basics.summary,
                self.skills,
                self.experience,
                self.projects,
                self.education,
                self.certificates,
                self.additional_sections,
            )
        )
        if not has_content:
            raise ValueError("resume contains no substantive content")
        return self


PDFRenderer = Callable[[str, Path], None]


def parse_resume_document(raw_response: str) -> ResumeDocument:
    """Parse a strict JSON model response into a validated resume document."""

    candidate = (raw_response or "").strip()
    fenced = _JSON_FENCE.fullmatch(candidate)
    if fenced:
        candidate = fenced.group(1)
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    try:
        payload = json.loads(candidate)
        return ResumeDocument.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        raise ResumeDocumentError("AI 返回的新版简历结构不符合要求，请重试") from exc


def render_resume_html(
    document: ResumeDocument,
    *,
    photo_data_uri: str | None = None,
) -> str:
    """Render only validated, escaped fields into the fixed print template."""

    basics = document.basics
    contacts = _render_contacts(basics)
    summary = (
        _section("个人简介", f"<p>{_text(basics.summary)}</p>")
        if basics.summary
        else ""
    )
    skills = _section("专业技能", _tag_list(document.skills)) if document.skills else ""
    experience = _render_entries("工作经历", document.experience, "company")
    projects = _render_entries("项目经历", document.projects, "name")
    education = _render_education(document.education)
    certificates = (
        _section("证书与资质", _bullet_list(document.certificates))
        if document.certificates
        else ""
    )
    additional_sections = "".join(
        _section(section.title, _bullet_list(section.highlights))
        for section in document.additional_sections
        if section.title and section.highlights
    )
    title = escape(basics.name or "个人简历")
    headline = (
        f'<p class="headline">{_text(basics.headline)}</p>' if basics.headline else ""
    )

    photo_src = escape(photo_data_uri or default_avatar_data_uri(), quote=True)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    @page {{ size: A4; margin: 14mm 16mm 16mm; }}
    * {{ box-sizing: border-box; }}
    html {{ color: #17202a; font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif; font-size: 10pt; line-height: 1.48; }}
    body {{ margin: 0; background: #fff; overflow-wrap: anywhere; }}
    header {{ align-items: flex-start; border-bottom: 2px solid #176b57; display: flex; gap: 7mm; justify-content: space-between; margin-bottom: 5mm; min-height: 36mm; padding-bottom: 3.5mm; }}
    .identity {{ min-width: 0; padding-top: 1mm; }}
    .resume-photo {{ background: #f2f4f5; border: 1px solid #cbd3d0; flex: 0 0 28mm; height: 36mm; object-fit: cover; width: 28mm; }}
    h1 {{ font-size: 24pt; line-height: 1.15; margin: 0; }}
    .headline {{ color: #176b57; font-size: 11.5pt; font-weight: 700; margin: 1.5mm 0 0; }}
    .contacts {{ color: #4a5560; display: flex; flex-wrap: wrap; gap: 1.2mm 4mm; margin-top: 2.5mm; }}
    .contact-item {{ display: inline-flex; min-width: 0; }}
    .contact-label {{ color: #2f3d46; flex: 0 0 auto; font-weight: 600; }}
    .contact-value {{ min-width: 0; overflow-wrap: anywhere; }}
    .contacts a {{ color: inherit; text-decoration: none; }}
    section {{ margin-top: 4.5mm; }}
    h2 {{ border-bottom: 1px solid #b9c5c1; color: #176b57; font-size: 12.5pt; margin: 0 0 2.2mm; padding-bottom: 1mm; break-after: avoid; }}
    p {{ margin: 0; white-space: pre-line; }}
    .entry {{ break-inside: avoid; margin: 0 0 3.2mm; }}
    .entry:last-child {{ margin-bottom: 0; }}
    .entry-head {{ align-items: baseline; display: flex; gap: 3mm; justify-content: space-between; }}
    .entry-title {{ font-size: 10.5pt; font-weight: 700; }}
    .entry-meta {{ color: #56616b; flex: 0 0 auto; font-size: 9pt; }}
    .entry-subtitle {{ color: #39434d; font-weight: 600; margin-top: .6mm; }}
    ul {{ margin: 1.2mm 0 0; padding-left: 5mm; }}
    li {{ margin: .7mm 0; padding-left: .8mm; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 1.5mm; list-style: none; margin: 0; padding: 0; }}
    .tags li {{ background: #eef4f2; border: 1px solid #cbd9d5; border-radius: 2px; margin: 0; padding: 1mm 2mm; }}
  </style>
</head>
<body>
  <header>
    <div class="identity">
      <h1>{title}</h1>
      {headline}
      {contacts}
    </div>
    <img class="resume-photo" src="{photo_src}" alt="简历照片">
  </header>
  <main>{summary}{skills}{experience}{projects}{education}{certificates}{additional_sections}</main>
</body>
</html>"""


def default_avatar_data_uri() -> str:
    """Return the bundled placeholder as a self-contained image URL."""

    try:
        payload = _DEFAULT_AVATAR_PATH.read_bytes()
    except OSError as exc:
        raise ResumePDFError("默认照片资源不可用，请联系管理员") from exc
    return _data_uri("image/svg+xml", payload)


def prepare_photo_data_uri(photo_file: str | Path | None) -> str:
    """Validate an optional local photo and return a browser-safe data URI."""

    if not photo_file:
        return default_avatar_data_uri()

    path = Path(photo_file)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ResumePDFError("简历照片无法读取，请重新上传") from exc
    if size <= 0:
        raise ResumePDFError("简历照片为空，请重新上传")
    if size > MAX_PHOTO_BYTES:
        raise ResumePDFError("简历照片不能超过 5MB")

    try:
        with Image.open(path) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ResumePDFError("简历照片仅支持 JPEG、PNG 或 WebP 格式")
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_PHOTO_PIXELS:
                raise ResumePDFError("简历照片尺寸无效或像素过大")
            photo = ImageOps.exif_transpose(source)
            photo.thumbnail(MAX_RENDERED_PHOTO_SIZE, Image.Resampling.LANCZOS)
            if photo.mode in {"RGBA", "LA"} or "transparency" in photo.info:
                rgba = photo.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                photo = flattened
            else:
                photo = photo.convert("RGB")
            output = BytesIO()
            photo.save(output, format="JPEG", quality=90, optimize=True)
    except ResumePDFError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise ResumePDFError("简历照片仅支持 JPEG、PNG 或 WebP 格式")
    return _data_uri("image/jpeg", output.getvalue())


def document_to_markdown(document: ResumeDocument) -> str:
    """Create an editable text representation of the generated resume."""

    basics = document.basics
    lines = [f"# {basics.name or '个人简历'}"]
    if basics.headline:
        lines.extend(["", basics.headline])
    contacts = " | ".join(
        value
        for value in (basics.phone, basics.email, basics.location, basics.website)
        if value
    )
    if contacts:
        lines.extend(["", contacts])
    if basics.summary:
        lines.extend(["", "## 个人简介", basics.summary])
    if document.skills:
        lines.extend(["", "## 专业技能", "、".join(document.skills)])
    _append_entries_markdown(lines, "工作经历", document.experience, "company")
    _append_entries_markdown(lines, "项目经历", document.projects, "name")
    if document.education:
        lines.extend(["", "## 教育经历"])
        for entry in document.education:
            qualification = " · ".join(
                value for value in (entry.degree, entry.major) if value
            )
            lines.append(
                " | ".join(
                    value
                    for value in (
                        entry.school or "教育经历",
                        qualification,
                        _date_range(entry.start_date, entry.end_date),
                    )
                    if value
                )
            )
            lines.extend(f"- {item}" for item in entry.highlights)
    if document.certificates:
        lines.extend(["", "## 证书与资质"])
        lines.extend(f"- {item}" for item in document.certificates)
    for section in document.additional_sections:
        if section.title and section.highlights:
            lines.extend(["", f"## {section.title}"])
            lines.extend(f"- {item}" for item in section.highlights)
    return "\n".join(lines).strip()


def create_resume_pdf(
    document: ResumeDocument,
    target_position: str,
    output_dir: str | Path,
    *,
    record_id: int | None = None,
    renderer: PDFRenderer | None = None,
    photo_data_uri: str | None = None,
) -> Path:
    """Render a validated resume to a stable A4 PDF via Playwright."""

    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename(target_position) or "新版简历"
    suffix = str(record_id) if record_id is not None else uuid4().hex[:8]
    destination = destination_dir / f"{stem}_新版简历_{suffix}.pdf"
    temporary = destination_dir / f".{destination.stem}.{uuid4().hex}.tmp.pdf"
    render = renderer or _render_pdf_with_playwright

    try:
        render(render_resume_html(document, photo_data_uri=photo_data_uri), temporary)
        if not temporary.is_file() or temporary.stat().st_size < 5:
            raise ResumePDFError("PDF 简历生成失败，请稍后重试")
        with temporary.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ResumePDFError("PDF 简历生成结果无效，请稍后重试")
        temporary.replace(destination)
    except ResumePDFError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ResumePDFError("PDF 简历生成失败，请稍后重试") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _render_pdf_with_playwright(html: str, destination: Path) -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ResumePDFError("PDF 生成组件未安装，请联系管理员") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.emulate_media(media="print")
                page.pdf(
                    path=str(destination),
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=(
                        '<div style="font-size:8px;color:#7b858d;width:100%;'
                        'text-align:center"><span class="pageNumber"></span> / '
                        '<span class="totalPages"></span></div>'
                    ),
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        message = str(exc).lower()
        if "executable doesn't exist" in message or "browser was not found" in message:
            detail = "PDF 渲染浏览器未安装，请运行 playwright install chromium"
        else:
            detail = "PDF 渲染失败，请稍后重试"
        raise ResumePDFError(detail) from exc


def _normalize_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized


def _data_uri(media_type: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _append_entries_markdown(
    lines: list[str], title: str, entries: list[object], primary_field: str
) -> None:
    if not entries:
        return
    lines.extend(["", f"## {title}"])
    for entry in entries:
        heading = getattr(entry, primary_field) or entry.role or "相关经历"
        details = " | ".join(
            value
            for value in (
                heading,
                entry.role if heading != entry.role else "",
                _date_range(entry.start_date, entry.end_date),
            )
            if value
        )
        lines.append(details)
        lines.extend(f"- {item}" for item in entry.highlights)


def _render_contacts(basics: ResumeBasics) -> str:
    values: list[str] = []
    if basics.phone:
        phone_href = re.sub(r"[^\d+]", "", basics.phone)
        values.append(
            '<span class="contact-item"><span class="contact-label">手机：</span>'
            f'<a class="contact-value" href="tel:{escape(phone_href, quote=True)}">'
            f"{_text(basics.phone)}</a></span>"
        )
    if basics.email:
        email_href = quote(basics.email, safe="@.+-_~")
        values.append(
            '<span class="contact-item"><span class="contact-label">邮箱：</span>'
            f'<a class="contact-value" href="mailto:{email_href}">'
            f"{_text(basics.email)}</a></span>"
        )
    if basics.location:
        values.append(
            '<span class="contact-item"><span class="contact-label">所在地：</span>'
            f'<span class="contact-value">{_text(basics.location)}</span></span>'
        )
    if basics.website:
        website_href = basics.website
        if not website_href.lower().startswith(("http://", "https://")):
            website_href = f"https://{website_href}"
        values.append(
            '<span class="contact-item"><span class="contact-label">个人主页：</span>'
            f'<a class="contact-value" href="{escape(website_href, quote=True)}">'
            f"{_text(basics.website)}</a></span>"
        )
    return f'<div class="contacts">{"".join(values)}</div>' if values else ""


def _render_entries(title: str, entries: list[object], primary_field: str) -> str:
    rendered: list[str] = []
    for entry in entries:
        primary = getattr(entry, primary_field)
        role = entry.role
        heading = primary or role or "相关经历"
        subtitle = role if primary and role else ""
        dates = _date_range(entry.start_date, entry.end_date)
        rendered.append(
            '<article class="entry">'
            f'<div class="entry-head"><div class="entry-title">{_text(heading)}</div>'
            f'<div class="entry-meta">{_text(dates)}</div></div>'
            + (
                f'<div class="entry-subtitle">{_text(subtitle)}</div>'
                if subtitle
                else ""
            )
            + _bullet_list(entry.highlights)
            + "</article>"
        )
    return _section(title, "".join(rendered)) if rendered else ""


def _render_education(entries: list[ResumeEducation]) -> str:
    rendered: list[str] = []
    for entry in entries:
        qualification = " · ".join(
            value for value in (entry.degree, entry.major) if value
        )
        rendered.append(
            '<article class="entry">'
            f'<div class="entry-head"><div class="entry-title">{_text(entry.school or "教育经历")}</div>'
            f'<div class="entry-meta">{_text(_date_range(entry.start_date, entry.end_date))}</div></div>'
            + (
                f'<div class="entry-subtitle">{_text(qualification)}</div>'
                if qualification
                else ""
            )
            + _bullet_list(entry.highlights)
            + "</article>"
        )
    return _section("教育经历", "".join(rendered)) if rendered else ""


def _section(title: str, body: str) -> str:
    return f"<section><h2>{escape(title)}</h2>{body}</section>"


def _bullet_list(values: list[str]) -> str:
    if not values:
        return ""
    return "<ul>" + "".join(f"<li>{_text(value)}</li>" for value in values) + "</ul>"


def _tag_list(values: list[str]) -> str:
    return (
        '<ul class="tags">'
        + "".join(f"<li>{_text(value)}</li>" for value in values)
        + "</ul>"
    )


def _date_range(start_date: str, end_date: str) -> str:
    if start_date and end_date:
        return f"{start_date} - {end_date}"
    return start_date or end_date


def _text(value: str) -> str:
    return escape(value, quote=True)


def _safe_filename(value: str) -> str:
    cleaned = _INVALID_FILENAME.sub("_", (value or "").strip())
    return cleaned.rstrip(" .")[:50]


__all__ = [
    "MAX_PHOTO_BYTES",
    "PDFRenderer",
    "ResumeAdditionalSection",
    "ResumeBasics",
    "ResumeDocument",
    "ResumeDocumentError",
    "ResumeEducation",
    "ResumeExperience",
    "ResumePDFError",
    "ResumeProject",
    "create_resume_pdf",
    "default_avatar_data_uri",
    "document_to_markdown",
    "parse_resume_document",
    "prepare_photo_data_uri",
    "render_resume_html",
]
