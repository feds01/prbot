import re
from datetime import datetime
from typing import override

from pydantic import BaseModel, field_validator

_PAGE_ID = re.compile(r"[0-9a-f]{32}")


class NotionPageId(BaseModel, frozen=True):
    """A Notion page ID: 32 lowercase hex characters, without dashes.

    Notion writes the same ID without dashes in links and with them in API
    responses, so both forms are accepted and stored one way.
    """

    value: str

    @field_validator("value", mode="before")
    @classmethod
    def _normalise(cls, raw: object) -> object:
        if not isinstance(raw, str):
            return raw
        value = raw.replace("-", "").lower()
        if not _PAGE_ID.fullmatch(value):
            msg = f"not a Notion page ID: {raw!r}"
            raise ValueError(msg)
        return value

    @override
    def __str__(self) -> str:
        return self.value


class ConsensusRow(BaseModel, frozen=True):
    """One row of an ADR's consensus table, as plain values.

    - ``reviewer``: who wrote the row, as a key that matches across rows: the
      Name cell's text, lowercased and without a mention's leading "@". Empty
      when the Name cell is.
    - ``version``: the ADR version the row reviews, or ``None`` when the cell is
      empty or isn't a whole number (people sometimes put a date there).
    - ``comment``: the Comments cell as plain text, score included.
    """

    reviewer: str
    version: int | None
    comment: str


class AdrSnapshot(BaseModel, frozen=True):
    """What an ADR page says right now: its version and its consensus table.

    ``version`` is the page's ``Version`` property, or ``None`` when it is
    unset or not a whole number.
    """

    page_id: NotionPageId
    version: int | None
    last_edited_time: datetime
    rows: tuple[ConsensusRow, ...] = ()
