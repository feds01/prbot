from typing import Protocol

from prbot.domain.tracking.value_objects import MessageRef


class MessageHandler(Protocol):
    """Anything that processes a message posted in a chat platform."""

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None: ...


class RouteMessage:
    """Use case: hand a new message to the pipeline that owns it."""

    def __init__(self, *, pr_messages: MessageHandler) -> None:
        self._pr_messages = pr_messages

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None:
        await self._pr_messages.execute(message_ref=message_ref, text=text, scope_keys=scope_keys)
