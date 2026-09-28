"""Repair mobile numbers joined to numeric QQ addresses during extraction."""

from __future__ import annotations

import re

# A numeric QQ ID has 5-11 digits; a longer local part can contain a mobile.
_JOINED_QQ_CONTACT = re.compile(
    r"(?<![A-Za-z0-9_.+%\-])(?P<phone>1[3-9][0-9]{9})"
    r"(?P<email>[1-9][0-9]{4,10}\\?@qq\.com)(?![A-Za-z0-9_.\-])",
    re.IGNORECASE,
)
_CONTACT_LABEL = re.compile(
    r"(?:电子邮箱|电子邮件|邮箱|电话|手机|e-?mail|phone|mobile)\s*[:：]\s*$",
    re.IGNORECASE,
)


def split_joined_qq_contact(value: str) -> tuple[str, str] | None:
    """Split only the bounded mobile + numeric QQ pattern, not arbitrary emails."""

    match = _JOINED_QQ_CONTACT.fullmatch(value.strip())
    if match is None:
        return None
    return match["phone"], match["email"].replace("\\@", "@")


def normalize_resume_contacts(text: str) -> str:
    """Restore contact boundaries in extracted text and model Markdown output."""

    # Consume a misleading contact label along with the joined address.
    parts: list[str] = []
    end = 0
    for match in _JOINED_QQ_CONTACT.finditer(text):
        prefix = text[end : match.start()]
        parts.append(_CONTACT_LABEL.sub("", prefix))
        email = match["email"].replace("\\@", "@")
        parts.append(f"电话：{match['phone']} | 邮箱：{email}")
        end = match.end()
    parts.append(text[end:])
    return "".join(parts)
