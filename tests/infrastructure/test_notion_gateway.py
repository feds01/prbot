from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from httpx import Response

from prbot.domain.adr.value_objects import ConsensusRow, NotionPageId
from prbot.infrastructure.notion_gateway import NotionGateway, RichText, read_consensus_table

PAGE = NotionPageId(value="40dd89c32a9981efa4a0d82e2b5ce7d8")
API = "https://api.notion.com/v1"
TOM = "3add872b-594c-81cd-a06d-0002307e3346"
ALEX = "28dd872b-594c-81ac-a8ec-0002a16c5da8"
YVES = "113d872b-594c-81d8-9190-00020fe16f51"

type Json = dict[str, object]


# --- Builders for Notion API JSON, trimmed to the fields that matter. ---


def text(content: str) -> Json:
    return {"type": "text", "plain_text": content, "text": {"content": content}}


def user(user_id: str, name: str) -> Json:
    return {
        "type": "mention",
        "plain_text": f"@{name}",
        "mention": {"type": "user", "user": {"object": "user", "id": user_id}},
    }


def date(start: str) -> Json:
    return {"type": "mention", "plain_text": start, "mention": {"type": "date", "date": {}}}


def page(version: float | None, edited: str = "2026-11-10T19:53:00.000Z") -> Json:
    return {
        "object": "page",
        "id": "40dd89c3-2a99-81ef-a4a0-d82e2b5ce7d8",
        "last_edited_time": edited,
        "properties": {
            "﻿Name": {"id": "title", "type": "title", "title": []},
            "Status": {"id": "s", "type": "select", "select": {"name": "Proposed"}},
            "Version": {"id": "v", "type": "number", "number": version},
        },
    }


def heading(block_id: str, content: str, *, level: int = 2, has_children: bool = False) -> Json:
    kind = f"heading_{level}"
    return {
        "id": block_id,
        "type": kind,
        "has_children": has_children,
        kind: {"rich_text": [text(content)]},
    }


def paragraph(block_id: str, content: str) -> Json:
    return {"id": block_id, "type": "paragraph", "paragraph": {"rich_text": [text(content)]}}


def block(block_id: str, kind: str) -> Json:
    return {"id": block_id, "type": kind, "has_children": True, kind: {}}


def row(*cells: list[Json]) -> Json:
    return {"id": "row", "type": "table_row", "table_row": {"cells": list(cells)}}


def children(*blocks: Json, next_cursor: str | None = None) -> Json:
    return {
        "object": "list",
        "results": list(blocks),
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
    }


def mock_children(block_id: str, *blocks: Json) -> respx.Route:
    return respx.get(f"{API}/blocks/{block_id}/children").mock(
        return_value=Response(200, json=children(*blocks))
    )


def mock_page(version: float | None = 4) -> respx.Route:
    return respx.get(f"{API}/pages/{PAGE}").mock(return_value=Response(200, json=page(version)))


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def gateway() -> AsyncIterator[NotionGateway]:
    gateway = NotionGateway("secret_token", sleep=_no_sleep, min_interval=0)
    yield gateway
    await gateway.close()


class TestFetchSnapshot:
    @respx.mock
    async def test_reads_a_table_with_a_date_column(self, gateway: NotionGateway) -> None:
        # Shaped like "Configurable deal amount field": an Alternatives table
        # comes first, then the consensus table under its heading.
        mock_page(version=4)
        mock_children(
            str(PAGE),
            heading("h1", "Alternatives Considered"),
            block("alternatives", "table"),
            heading("h2", "Consensus"),
            paragraph("p", "Add your name and how strongly you agree..."),
            block("consensus", "table"),
        )
        alternatives = mock_children("alternatives")
        mock_children(
            "consensus",
            row([text("Name")], [text("Version")], [text("Date")], [text("Comments")]),
            row([user(TOM, "Tom")], [text("3")], [date("2026-11-03")], [text("+2")]),
            row([user(ALEX, "Alex")], [text("2")], [date("2026-11-03")], [text("+1")]),
            row([], [], [], []),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.page_id == PAGE
        assert snapshot.version == 4
        assert snapshot.last_edited_time == datetime(2026, 11, 10, 19, 53, tzinfo=UTC)
        assert snapshot.rows == (
            ConsensusRow(reviewer="tom", version=3, comment="+2"),
            ConsensusRow(reviewer="alex", version=2, comment="+1"),
        )
        assert not alternatives.called

    @respx.mock
    async def test_reads_a_table_without_a_date_column(self, gateway: NotionGateway) -> None:
        # Shaped like "Audience health emails": no Date column, a date typed
        # into one Version cell, and multi-line comments.
        mock_page(version=3)
        mock_children(str(PAGE), heading("h", "Consensus", level=1), block("t", "table"))
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([user(YVES, "Yves")], [date("November 11, 2026")], [text("+1")]),
            row(
                [user(YVES, "Yves")],
                [text("2")],
                [text("+0.5\n\n"), text("I am not too clear on the producer")],
            ),
            row([user(ALEX, "Alex")], [text("3")], [text("+1 LGTM")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (
            ConsensusRow(reviewer="yves", version=None, comment="+1"),
            ConsensusRow(
                reviewer="yves", version=2, comment="+0.5\n\nI am not too clear on the producer"
            ),
            ConsensusRow(reviewer="alex", version=3, comment="+1 LGTM"),
        )

    @respx.mock
    async def test_sends_the_token_and_api_version(self, gateway: NotionGateway) -> None:
        page_route = mock_page()
        mock_children(str(PAGE))

        await gateway.fetch_snapshot(PAGE)

        request = page_route.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret_token"
        assert request.headers["Notion-Version"] == "2025-09-03"

    @respx.mock
    async def test_follows_pagination(self, gateway: NotionGateway) -> None:
        mock_page()
        respx.get(f"{API}/blocks/{PAGE}/children", params={"start_cursor": "next"}).mock(
            return_value=Response(200, json=children(block("t", "table")))
        )
        respx.get(f"{API}/blocks/{PAGE}/children").mock(
            return_value=Response(200, json=children(paragraph("p", "..."), next_cursor="next"))
        )
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([text("Tom")], [text("4")], [text("+1")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (ConsensusRow(reviewer="tom", version=4, comment="+1"),)

    @respx.mock
    async def test_finds_a_table_inside_a_toggle(self, gateway: NotionGateway) -> None:
        mock_page()
        mock_children(str(PAGE), heading("h", "Consensus", has_children=True))
        mock_children("h", block("t", "table"))
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([text("Tom")], [text("4")], [text("-1")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (ConsensusRow(reviewer="tom", version=4, comment="-1"),)

    @respx.mock
    async def test_finds_a_table_inside_a_column(self, gateway: NotionGateway) -> None:
        mock_page()
        mock_children(str(PAGE), block("cols", "column_list"))
        mock_children("cols", block("col", "column"))
        mock_children("col", block("t", "table"))
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([text("Tom")], [text("4")], [text("+2")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (ConsensusRow(reviewer="tom", version=4, comment="+2"),)

    @respx.mock
    async def test_prefers_a_consensus_toggle_heading_to_other_tables(
        self, gateway: NotionGateway
    ) -> None:
        # A changelog table with its own Version and Comments columns sits at
        # the top level; the consensus table is inside a toggle heading.
        mock_page()
        mock_children(
            str(PAGE),
            block("changelog", "table"),
            heading("h", "Consensus", has_children=True),
        )
        changelog = mock_children(
            "changelog",
            row([text("Version")], [text("Date")], [text("Comments")]),
            row([text("1")], [text("2026-11-01")], [text("First draft")]),
        )
        mock_children("h", block("t", "table"))
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([text("Tom")], [text("4")], [text("+1")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (ConsensusRow(reviewer="tom", version=4, comment="+1"),)
        assert not changelog.called

    @respx.mock
    async def test_a_subheading_stays_in_the_consensus_section(
        self, gateway: NotionGateway
    ) -> None:
        mock_page()
        mock_children(
            str(PAGE),
            block("changelog", "table"),
            heading("h1", "Consensus", level=1),
            heading("h2", "Round 2", level=3),
            block("t", "table"),
            heading("h3", "Appendix", level=1),
            block("appendix", "table"),
        )
        changelog = mock_children("changelog")
        appendix = mock_children("appendix")
        mock_children(
            "t",
            row([text("Name")], [text("Version")], [text("Comments")]),
            row([text("Tom")], [text("4")], [text("+2")]),
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == (ConsensusRow(reviewer="tom", version=4, comment="+2"),)
        assert not changelog.called
        assert not appendix.called

    @respx.mock
    async def test_stops_reading_a_table_once_its_header_rules_it_out(
        self, gateway: NotionGateway
    ) -> None:
        mock_page()
        mock_children(str(PAGE), block("big", "table"))
        rest = respx.get(f"{API}/blocks/big/children", params={"start_cursor": "more"}).mock(
            return_value=Response(200, json=children())
        )
        respx.get(f"{API}/blocks/big/children").mock(
            return_value=Response(
                200,
                json=children(row([text("Option")], [text("Pros")]), next_cursor="more"),
            )
        )

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.rows == ()
        assert not rest.called

    @respx.mock
    async def test_no_consensus_table_means_no_rows(self, gateway: NotionGateway) -> None:
        mock_page()
        mock_children(str(PAGE), block("t", "table"))
        mock_children("t", row([text("Option")], [text("Pros")], [text("Cons")]))

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.version == 4
        assert snapshot.rows == ()

    @pytest.mark.parametrize(("number", "version"), [(2, 2), (2.0, 2), (2.5, None), (None, None)])
    @respx.mock
    async def test_page_version(
        self, gateway: NotionGateway, number: float | None, version: int | None
    ) -> None:
        mock_page(version=number)
        mock_children(str(PAGE))

        snapshot = await gateway.fetch_snapshot(PAGE)

        assert snapshot.version == version

    @respx.mock
    async def test_retries_after_a_rate_limit(self) -> None:
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        gateway = NotionGateway("t", sleep=sleep, min_interval=0)
        respx.get(f"{API}/pages/{PAGE}").mock(
            side_effect=[
                Response(429, headers={"Retry-After": "2"}),
                Response(200, json=page(1)),
            ]
        )
        mock_children(str(PAGE))

        snapshot = await gateway.fetch_snapshot(PAGE)
        await gateway.close()

        assert snapshot.version == 1
        assert delays == [2.0]

    @respx.mock
    async def test_gives_up_after_repeated_rate_limits(self, gateway: NotionGateway) -> None:
        route = respx.get(f"{API}/pages/{PAGE}").mock(return_value=Response(429))

        with pytest.raises(httpx.HTTPStatusError):
            await gateway.fetch_snapshot(PAGE)
        assert route.call_count == 3

    @respx.mock
    async def test_spaces_requests_out(self) -> None:
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        # The clock stands still, so every request after the first waits its turn.
        gateway = NotionGateway("t", sleep=sleep, clock=lambda: 100.0)
        mock_page()
        mock_children(str(PAGE))

        await gateway.fetch_snapshot(PAGE)
        await gateway.fetch_snapshot(PAGE)
        await gateway.close()

        assert delays == pytest.approx([1 / 3, 2 / 3, 1.0])

    @respx.mock
    async def test_does_not_wait_when_requests_are_already_spaced(self) -> None:
        delays: list[float] = []
        times = iter([100.0, 101.0])

        async def sleep(seconds: float) -> None:
            delays.append(seconds)

        gateway = NotionGateway("t", sleep=sleep, clock=lambda: next(times))
        mock_page()
        mock_children(str(PAGE))

        await gateway.fetch_snapshot(PAGE)
        await gateway.close()

        assert delays == []

    @respx.mock
    async def test_raises_when_the_page_is_not_shared(self, gateway: NotionGateway) -> None:
        respx.get(f"{API}/pages/{PAGE}").mock(
            return_value=Response(404, json={"object": "error", "code": "object_not_found"})
        )

        with pytest.raises(httpx.HTTPStatusError):
            await gateway.fetch_snapshot(PAGE)


def cells(*values: str) -> list[list[RichText]]:
    return [[RichText(plain_text=value)] if value else [] for value in values]


class TestReadConsensusTable:
    def test_finds_columns_by_name_in_any_order(self) -> None:
        rows = [
            cells("Comments", "Name", "Date", "Version"),
            cells("+1", "Tom", "2026-11-03", "2"),
        ]
        assert read_consensus_table(rows) == (
            ConsensusRow(reviewer="tom", version=2, comment="+1"),
        )

    def test_header_names_are_case_insensitive_and_may_be_singular(self) -> None:
        rows = [cells("NAME", "version", "Comment"), cells("Tom", "1", "-1")]
        assert read_consensus_table(rows) == (
            ConsensusRow(reviewer="tom", version=1, comment="-1"),
        )

    @pytest.mark.parametrize(
        ("cell", "version"),
        [("2", 2), ("v2", 2), ("V3", 3), ("2.0", 2), ("2.5", None), ("", None), ("Nov 11", None)],
    )
    def test_version_cells(self, cell: str, version: int | None) -> None:
        rows = [cells("Name", "Version", "Comments"), cells("Tom", cell, "+1")]
        assert read_consensus_table(rows) == (
            ConsensusRow(reviewer="tom", version=version, comment="+1"),
        )

    @pytest.mark.parametrize("name", ["Name", "Reviewer", "Who"])
    def test_reviewer_column(self, name: str) -> None:
        rows = [cells(name, "Date", "Version", "Comments"), cells("Tom", "Nov 3", "1", "+1")]
        assert read_consensus_table(rows) == (
            ConsensusRow(reviewer="tom", version=1, comment="+1"),
        )

    def test_a_date_column_is_never_the_reviewer(self) -> None:
        rows = [cells("Date", "Version", "Comments"), cells("Nov 3", "1", "+1")]
        assert read_consensus_table(rows) == (ConsensusRow(reviewer="", version=1, comment="+1"),)

    def test_a_mentioned_and_a_typed_name_are_the_same_reviewer(self) -> None:
        rows = [
            cells("Name", "Version", "Comments"),
            cells("@Alex Fedotov", "2", "-1"),
            cells("alex  fedotov", "2", "+1"),
        ]
        reviewers = {r.reviewer for r in read_consensus_table(rows) or ()}
        assert reviewers == {"alex fedotov"}

    def test_a_missing_name_column_leaves_reviewers_blank(self) -> None:
        rows = [cells("Version", "Comments"), cells("1", "+1")]
        assert read_consensus_table(rows) == (ConsensusRow(reviewer="", version=1, comment="+1"),)

    @pytest.mark.parametrize(
        "header",
        [cells("Name", "Comments"), cells("Name", "Version"), cells("Option", "Pros", "Cons")],
    )
    def test_other_tables_are_not_consensus_tables(self, header: list[list[RichText]]) -> None:
        assert read_consensus_table([header]) is None

    def test_an_empty_table_is_not_a_consensus_table(self) -> None:
        assert read_consensus_table([]) is None
