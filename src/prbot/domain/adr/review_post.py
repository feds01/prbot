"""Recognising a chat message that asks for an ADR review."""

import re
from urllib.parse import parse_qs, urlsplit

from prbot.domain.adr.value_objects import NotionPageId

# ":memo: ADR to review" or "ADRs to review", maybe with the emoji or words
# in bold or italics.
_MARKER = re.compile(r"[*_]*(?::memo:|\N{MEMO})[*_]*\s*[*_]*\s*ADRs? to review", re.IGNORECASE)
# A Notion URL, up to where Slack's <url|title> markup or whitespace ends it.
_NOTION_URL = re.compile(r"https?://(?:[\w-]+\.)*notion\.(?:so|site|com)/[^\s|>]*", re.IGNORECASE)
_PAGE_ID = re.compile(r"[0-9a-fA-F]{32}")
# Links end in the page ID, either alone (/p/<id>) or after a slug (/<Title>-<id>).
_PAGE_ID_AT_END = re.compile(r"[/-]([0-9a-fA-F]{32})/?$")


def find_adr_review(text: str) -> NotionPageId | None:
    """Return the ADR a review post links to, or None if *text* isn't an ADR review post.

    A review post says ":memo: ADR to review" on its first line and links to
    the ADR in Notion; the first Notion page linked after those words is the
    ADR. Links to a database view are passed over.

    The words have to be on the first line because *text* can go on with the
    text of other messages this one links to. A PR post that links to an ADR
    review post is not itself one.
    """
    text = text.lstrip()
    first_line = text.partition("\n")[0]
    if not (marker := _MARKER.search(first_line)):
        return None
    for url in _NOTION_URL.findall(text, marker.end()):
        if page_id := _page_id(url):
            return page_id
    return None


def _page_id(url: str) -> NotionPageId | None:
    # Slack writes "&" in message text as "&amp;".
    parts = urlsplit(url.replace("&amp;", "&"))
    # A page open in a database's side peek: /<database>?v=<view>&p=<page>.
    query = parse_qs(parts.query)
    peeked = query.get("p", [""])[0]
    if _PAGE_ID.fullmatch(peeked):
        return NotionPageId(value=peeked)
    if "v" in query:
        # A database view (/<database>?v=<view>): the ID is the database's.
        return None
    # The path, not the fragment: that can name a block, whose ID looks the same.
    if match := _PAGE_ID_AT_END.search(parts.path):
        return NotionPageId(value=match.group(1))
    return None
