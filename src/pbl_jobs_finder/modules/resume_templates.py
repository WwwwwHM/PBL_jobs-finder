"""Reviewed local presentation templates for structured resume PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ResumeTemplate:
    """Trusted style tokens for one immutable resume presentation."""

    id: str
    label: str
    page_margin: str
    font_size: str
    line_height: str
    ink: str
    muted: str
    accent: str
    accent_secondary: str
    line: str
    photo_background: str
    tag_background: str
    layout_css: str


_TEMPLATES = MappingProxyType(
    {
        "classic": ResumeTemplate(
            id="classic",
            label="经典专业",
            page_margin="14mm 16mm 16mm",
            font_size="10pt",
            line_height="1.48",
            ink="#17202a",
            muted="#56616b",
            accent="#176b57",
            accent_secondary="#8f5b2e",
            line="#b9c5c1",
            photo_background="#f2f4f5",
            tag_background="#eef4f2",
            layout_css="""
    header { border-bottom-width: 2px; min-height: 36mm; }
    h1 { font-size: 24pt; }
    .resume-photo { flex-basis: 28mm; height: 36mm; width: 28mm; }
""",
        ),
        "compact": ResumeTemplate(
            id="compact",
            label="紧凑清晰",
            page_margin="10mm 12mm 12mm",
            font_size="9pt",
            line_height="1.36",
            ink="#20272d",
            muted="#5b666e",
            accent="#2f4858",
            accent_secondary="#b34f44",
            line="#aeb8bd",
            photo_background="#f1f3f4",
            tag_background="#edf1f2",
            layout_css="""
    header { border-bottom-width: 1px; margin-bottom: 3.5mm; min-height: 30mm; padding-bottom: 2.5mm; }
    h1 { font-size: 21pt; }
    .headline { color: var(--accent-secondary); font-size: 10.5pt; }
    .resume-photo { flex-basis: 23mm; height: 30mm; width: 23mm; }
    section { margin-top: 3.2mm; }
    h2 { border-left: 2.5mm solid var(--accent-secondary); font-size: 11pt; padding: .5mm 0 .5mm 2mm; }
    .entry { margin-bottom: 2.4mm; }
    li { margin: .45mm 0; }
""",
        ),
        "technical": ResumeTemplate(
            id="technical",
            label="技术重点",
            page_margin="13mm 15mm 15mm",
            font_size="9.5pt",
            line_height="1.44",
            ink="#18232d",
            muted="#52606b",
            accent="#235789",
            accent_secondary="#b06f18",
            line="#a9b9c7",
            photo_background="#edf2f6",
            tag_background="#eaf1f7",
            layout_css="""
    header { border-bottom: 3px double var(--accent); }
    h1 { font-size: 23pt; }
    .headline { color: var(--accent-secondary); }
    h2 { border-bottom-color: var(--accent-secondary); }
    .tags li { border-color: #b7cad9; font-family: Consolas, "Microsoft YaHei", sans-serif; }
    .entry-title { color: var(--accent); }
""",
        ),
    }
)

DEFAULT_RESUME_TEMPLATE_ID = "classic"
RESUME_TEMPLATE_CHOICES = tuple(
    (template.label, template.id) for template in _TEMPLATES.values()
)


def get_resume_template(
    template_id: str = DEFAULT_RESUME_TEMPLATE_ID,
) -> ResumeTemplate:
    """Return a reviewed template by its exact stable identifier."""

    normalized = (template_id or DEFAULT_RESUME_TEMPLATE_ID).strip()
    try:
        return _TEMPLATES[normalized]
    except KeyError as exc:
        raise ValueError(f"未知的简历模板：{normalized}") from exc


__all__ = [
    "DEFAULT_RESUME_TEMPLATE_ID",
    "RESUME_TEMPLATE_CHOICES",
    "ResumeTemplate",
    "get_resume_template",
]
