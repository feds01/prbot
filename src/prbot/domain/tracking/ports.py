from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prbot.domain.tracking.entities import TrackedPR
    from prbot.domain.tracking.value_objects import MessageRef, PRInfo, PRUrl


class SourceRateLimitError(Exception):
    """The source has no request budget left until *reset_at*.

    Distinct from an ordinary fetch failure: retrying costs quota that is not
    there, so callers doing bulk work should stop rather than skip ahead.
    """

    def __init__(self, reset_at: float | None = None) -> None:
        super().__init__("source rate limit exhausted")
        self.reset_at = reset_at


class PRSourcePort(Protocol):
    """Port for an input source that can recognise and fetch PR references.

    Each implementation (GitHub, GitLab, …) knows its own URL patterns
    and how to retrieve PR metadata from its API.
    """

    def extract_pr_references(self, text: str) -> list[PRUrl]:
        """Return all PR references found in *text* that this source understands."""
        ...

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        """Fetch current state for a recognised PR reference."""
        ...


class ReactionPort(Protocol):
    """Port for adding emoji reactions to messages in any messaging platform."""

    async def add_reaction(
        self,
        message_ref: MessageRef,
        emoji: str,
        fallback_emoji: str | None = None,
    ) -> None:
        """Add a reaction to a message.

        If the primary emoji fails (e.g. not present in the target guild or
        workspace), the adapter may retry with `fallback_emoji` when provided.
        """
        ...


@runtime_checkable
class PRRepositoryPort(Protocol):
    """Port for persisting tracked PRs."""

    async def save(self, tracked_pr: TrackedPR) -> None: ...

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]: ...

    async def find_distinct_pr_urls(self, since: str | None = None) -> Sequence[PRUrl]:
        """Distinct tracked PRs, most recently active first.

        When *since* is given, only PRs touched at or after that timestamp.
        """
        ...

    async def add_emoji(
        self,
        pr_url: PRUrl,
        message_ref: MessageRef,
        emoji: str,
    ) -> None: ...


class ChannelCursorPort(Protocol):
    """Port for tracking the last-seen message timestamp per channel."""

    async def get_cursor(self, integration_id: str, channel_id: str) -> str | None: ...

    async def upsert_cursor(self, integration_id: str, channel_id: str, ts: str) -> None:
        """Advance the cursor. Must be monotonic — never moves backward."""
        ...
