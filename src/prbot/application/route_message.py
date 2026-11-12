from typing import Protocol

from prbot.domain.adr.review_post import find_adr_review
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.tracking.value_objects import MessageRef


class MessageHandler(Protocol):
    """Anything that processes a message posted in a chat platform."""

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None: ...


class AdrMessageHandler(Protocol):
    """Processes a post asking for review of the ADR at *page_id*."""

    async def execute(
        self,
        message_ref: MessageRef,
        page_id: NotionPageId,
        scope_keys: list[str] | None = None,
    ) -> None: ...


class RouteMessage:
    """Use case: hand a new message to the one pipeline that owns it.

    An ADR review post goes to the ADR pipeline only, even when it links to a
    PR too: both pipelines use the same emoji names, and Slack keeps one
    reaction per name, so each would take the other's reactions off. Without
    an ADR pipeline, every message goes to the PR pipeline.
    """

    def __init__(
        self,
        *,
        pr_messages: MessageHandler,
        adr_messages: AdrMessageHandler | None = None,
    ) -> None:
        self._pr_messages = pr_messages
        self._adr_messages = adr_messages

    def is_adr_review(self, text: str) -> bool:
        """Whether *text* is an ADR review post that this router sends to the ADR pipeline."""
        return self._adr_messages is not None and find_adr_review(text) is not None

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None:
        if self._adr_messages is not None and (page_id := find_adr_review(text)):
            await self._adr_messages.execute(
                message_ref=message_ref, page_id=page_id, scope_keys=scope_keys
            )
            return
        await self._pr_messages.execute(message_ref=message_ref, text=text, scope_keys=scope_keys)
