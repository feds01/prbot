from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import AdrSnapshot, NotionPageId
from prbot.domain.tracking.value_objects import MessageRef


class AdrSourcePort(Protocol):
    """Port for reading an ADR page: its current version and consensus table."""

    async def fetch_snapshot(self, page_id: NotionPageId) -> AdrSnapshot:
        """Read the page as it is now.

        A page without a consensus table reads as a snapshot with no rows.
        """
        ...


class AdrRepositoryPort(Protocol):
    """Port for persisting the ADR review posts being tracked."""

    async def save(self, tracked: TrackedADR) -> None:
        """Insert the post, or replace what is stored for it."""
        ...

    async def get(self, page_id: NotionPageId, message_ref: MessageRef) -> TrackedADR | None:
        """Return the stored post for this ADR and message, if any."""
        ...

    async def find_changed_since(self, since: datetime) -> Sequence[TrackedADR]:
        """Return the posts saved at or after *since*.

        A post is saved when it is first tracked and whenever its reactions
        or the ADR edit they reflect change, so this is the posts still active.
        """
        ...
