"""What a post is, and the hash an approval is bound to."""

from __future__ import annotations

import re
import urllib.parse

from apps.approvals.hashing import payload_sha256

from ..errors import InvalidPostError

MAX_TEXT = 2000
MAX_LINK = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    value = text.replace("\r\n", "\n").strip()
    if not value:
        raise InvalidPostError("The post text is empty.")
    if len(value) > MAX_TEXT:
        raise InvalidPostError(f"The post text is longer than {MAX_TEXT} characters.")
    if _CONTROL.search(value):
        raise InvalidPostError("The post text contains control characters.")
    return value


def clean_link(link: str) -> str:
    value = link.strip()
    if not value:
        return ""
    if len(value) > MAX_LINK:
        raise InvalidPostError(f"The link is longer than {MAX_LINK} characters.")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise InvalidPostError("The link must be an https address.")
    if parsed.username or parsed.password:
        raise InvalidPostError("The link must not contain a user name or password.")
    if _CONTROL.search(value) or " " in value:
        raise InvalidPostError("The link contains invalid characters.")
    return value


def content_hash(page_id: str, text: str, link: str) -> str:
    """The exact thing an owner approves: this Page, this text, this link."""
    return payload_sha256({"v": 1, "page": page_id, "text": text, "link": link})


def comparable(text: str) -> str:
    """Whitespace-insensitive form used to find our own post on the Page."""
    return " ".join(text.split())
