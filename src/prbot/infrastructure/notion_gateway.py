"""Reads ADR pages from the Notion API: the ``Version`` property and the consensus table."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Self

import httpx
from pydantic import BaseModel, Field

from prbot.domain.adr.value_objects import AdrSnapshot, ConsensusRow, NotionPageId

logger = logging.getLogger(__name__)

_API_URL = "https://api.notion.com/v1"
_NOTION_VERSION = "2025-09-03"
# Notion's maximum page size for block children.
_PAGE_SIZE = 100

# Notion allows about 3 requests a second and answers 429 with Retry-After.
_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_AFTER = 1.0
_MAX_RETRY_AFTER = 30.0

_VERSION_PROPERTY = "version"
_HEADING_LEVELS = {"heading_1": 1, "heading_2": 2, "heading_3": 3}
# Blocks a consensus table can sit inside, e.g. a toggle or a column.
_CONTAINER_TYPES = frozenset(
    {"toggle", "column_list", "column", "callout", "quote", "synced_block", *_HEADING_LEVELS}
)
# Enough for a table inside a column (column_list → column → table).
_MAX_NESTING = 2
# "2", "2.0" or "v2"; anything else, e.g. a date, isn't a version.
_WHOLE_NUMBER = re.compile(r"v?(\d+)(?:\.0+)?", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


# --- The parts of Notion's JSON we read; everything else is ignored. ---


class RichText(BaseModel):
    """One run of text in a Notion cell or heading. A mention's text is "@Name"."""

    plain_text: str = ""


class _Heading(BaseModel):
    rich_text: list[RichText] = Field(default_factory=list)


class _TableRow(BaseModel):
    cells: list[list[RichText]]


class _Block(BaseModel):
    id: str
    type: str
    has_children: bool = False
    heading_1: _Heading | None = None
    heading_2: _Heading | None = None
    heading_3: _Heading | None = None
    table_row: _TableRow | None = None

    @property
    def heading(self) -> tuple[int, str] | None:
        """The heading's level and text, if this block is a heading."""
        level = _HEADING_LEVELS.get(self.type)
        heading = self.heading_1 or self.heading_2 or self.heading_3
        if level is None or heading is None:
            return None
        return level, _plain(heading.rich_text)


class _BlockList(BaseModel):
    results: list[_Block]
    has_more: bool = False
    next_cursor: str | None = None


class _Property(BaseModel):
    type: str
    number: float | None = None


class _Page(BaseModel):
    last_edited_time: datetime
    properties: dict[str, _Property] = Field(default_factory=dict)


Cell = Sequence[RichText]


class NotionGateway:
    """Reads ADR pages with a Notion internal integration's token.

    The integration has to be added as a connection on the ADR database;
    until it is, every page reads as not found (404).
    """

    def __init__(
        self,
        token: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
            },
            timeout=10.0,
        )
        self._sleep = sleep

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_snapshot(self, page_id: NotionPageId) -> AdrSnapshot:
        page = _Page.model_validate(await self._get(f"/pages/{page_id}"))
        rows = await self._find_consensus_rows(str(page_id), _MAX_NESTING)
        if rows is None:
            logger.info("No consensus table found on ADR %s", page_id)
        return AdrSnapshot(
            page_id=page_id,
            version=_page_version(page),
            last_edited_time=page.last_edited_time,
            rows=rows or (),
        )

    async def _find_consensus_rows(
        self, block_id: str, nesting: int
    ) -> tuple[ConsensusRow, ...] | None:
        """Find the consensus table under *block_id* and read its rows.

        See ``_search_order`` for the order blocks are tried in.
        """
        blocks = [block async for block in self._children(block_id)]
        for block in _search_order(blocks, descend=nesting > 0):
            if block.type == "table":
                found = await self._read_table(block.id)
            else:
                found = await self._find_consensus_rows(block.id, nesting - 1)
            if found is not None:
                return found
        return None

    async def _read_table(self, table_id: str) -> tuple[ConsensusRow, ...] | None:
        """Read a table if it is a consensus table, judging by its first row.

        The header is checked before any further pages of rows are fetched,
        so rejecting a long table costs one request.
        """
        columns: _ConsensusColumns | None = None
        rows: list[ConsensusRow] = []
        async with aclosing(self._children(table_id)) as blocks:
            async for block in blocks:
                if block.table_row is None:
                    continue
                if columns is None:
                    columns = _ConsensusColumns.from_header(block.table_row.cells)
                    if columns is None:
                        return None
                elif (row := columns.read(block.table_row.cells)) is not None:
                    rows.append(row)
        return None if columns is None else tuple(rows)

    async def _children(self, block_id: str) -> AsyncGenerator[_Block]:
        params: dict[str, str | int] = {"page_size": _PAGE_SIZE}
        while True:
            page = _BlockList.model_validate(
                await self._get(f"/blocks/{block_id}/children", params)
            )
            for block in page.results:
                yield block
            if not page.has_more or page.next_cursor is None:
                return
            params["start_cursor"] = page.next_cursor

    async def _get(self, path: str, params: Mapping[str, str | int] | None = None) -> object:
        attempt = 1
        while True:
            resp = await self._client.get(path, params=params)
            if resp.status_code == HTTPStatus.TOO_MANY_REQUESTS and attempt < _MAX_ATTEMPTS:
                delay = _retry_after(resp)
                logger.info("Notion rate limit on %s, retrying in %.1fs", path, delay)
                await self._sleep(delay)
                attempt += 1
                continue
            if resp.is_error:
                # A 404 usually means the page isn't shared with the integration.
                logger.warning(
                    "Notion GET %s -> %d | body=%s", path, resp.status_code, resp.text[:500]
                )
                resp.raise_for_status()
            return resp.json()


@dataclass(frozen=True)
class _ConsensusColumns:
    """Where a consensus table keeps each value, found from its header row.

    Columns are found by name because tables differ: some have a Date column,
    some don't. A Version and a Comments column are required; without a Name
    or Reviewer column, the first other column (but not a Date) holds the reviewer.
    """

    reviewer: int | None
    version: int
    comment: int

    @classmethod
    def from_header(cls, header: Sequence[Cell]) -> Self | None:
        names = [_plain(cell).lower() for cell in header]
        version = _column(names, "version")
        comment = _column(names, "comment")
        if version is None or comment is None:
            return None
        reviewer = _column(names, "name", "reviewer")
        if reviewer is None:
            taken = {version, comment}
            others = (i for i, name in enumerate(names) if i not in taken and "date" not in name)
            reviewer = next(others, None)
        return cls(reviewer=reviewer, version=version, comment=comment)

    def read(self, row: Sequence[Cell]) -> ConsensusRow | None:
        """Read one row, or return ``None`` for a row with nothing in it."""
        if not any(_plain(cell) for cell in row):
            return None

        def cell(col: int | None) -> Cell:
            return row[col] if col is not None and col < len(row) else ()

        return ConsensusRow(
            reviewer=_reviewer(cell(self.reviewer)),
            version=_whole_number(_plain(cell(self.version))),
            comment=_plain(cell(self.comment)),
        )


def read_consensus_table(rows: Sequence[Sequence[Cell]]) -> tuple[ConsensusRow, ...] | None:
    """Read a table's rows if it is a consensus table, else return ``None``."""
    if not rows or (columns := _ConsensusColumns.from_header(rows[0])) is None:
        return None
    return tuple(row for cells in rows[1:] if (row := columns.read(cells)) is not None)


def _search_order(blocks: Sequence[_Block], *, descend: bool) -> list[_Block]:
    """Order the tables, and blocks to look inside, by where a consensus table likely is.

    The "Consensus" section comes first: its tables, and its toggles and
    columns, including the heading itself when it is a toggle. ADRs have other
    tables too, some with Version and Comments columns of their own. Then the
    other tables, then the other blocks to look inside, since each of those
    costs a request. A section lasts until a heading at its level or above.
    """
    in_section: list[_Block] = []
    tables: list[_Block] = []
    containers: list[_Block] = []
    section_level: int | None = None
    for block in blocks:
        if (heading := block.heading) is not None:
            level, text = heading
            if section_level is None or level <= section_level:
                section_level = level if "consensus" in text.lower() else None

        nested = descend and block.has_children and block.type in _CONTAINER_TYPES
        if block.type != "table" and not nested:
            continue
        if section_level is not None:
            in_section.append(block)
        else:
            (tables if block.type == "table" else containers).append(block)
    return in_section + tables + containers


def _column(names: Sequence[str], *words: str) -> int | None:
    return next((i for i, name in enumerate(names) if any(w in name for w in words)), None)


def _plain(cell: Cell) -> str:
    return "".join(part.plain_text for part in cell).strip()


def _reviewer(cell: Cell) -> str:
    """Key a reviewer by name, so "@Alex Fedotov" and a typed "Alex Fedotov" match."""
    name = _plain(cell).removeprefix("@")
    return _WHITESPACE.sub(" ", name).strip().casefold()


def _whole_number(text: str) -> int | None:
    match = _WHOLE_NUMBER.fullmatch(text)
    return int(match.group(1)) if match else None


def _page_version(page: _Page) -> int | None:
    for name, prop in page.properties.items():
        if name.strip().lower() == _VERSION_PROPERTY and prop.type == "number":
            number = prop.number
            return int(number) if number is not None and number.is_integer() else None
    return None


def _retry_after(resp: httpx.Response) -> float:
    try:
        delay = float(resp.headers["retry-after"])
    except KeyError, ValueError:
        return _DEFAULT_RETRY_AFTER
    return min(max(delay, 0.0), _MAX_RETRY_AFTER)
