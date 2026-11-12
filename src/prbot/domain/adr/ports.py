from typing import Protocol

from prbot.domain.adr.value_objects import AdrSnapshot, NotionPageId


class AdrSourcePort(Protocol):
    """Port for reading an ADR page: its current version and consensus table."""

    async def fetch_snapshot(self, page_id: NotionPageId) -> AdrSnapshot:
        """Read the page as it is now.

        A page without a consensus table reads as a snapshot with no rows.
        """
        ...
