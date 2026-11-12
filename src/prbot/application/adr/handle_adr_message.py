import logging

from prbot.application.adr.update_adr_post import UpdateAdrPost
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.ports import AdrRepositoryPort, AdrSourcePort
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.emoji.ports import EmojiConfigResolverPort
from prbot.domain.tracking.ports import ReactionPort
from prbot.domain.tracking.value_objects import MessageRef

logger = logging.getLogger(__name__)


class HandleAdrMessage:
    """Use case: an ADR review post arrives; react with its ADR's version and review state."""

    def __init__(
        self,
        *,
        source: AdrSourcePort,
        reactions: ReactionPort,
        adr_repository: AdrRepositoryPort,
        emoji_resolver: EmojiConfigResolverPort,
    ) -> None:
        self._source = source
        self._update = UpdateAdrPost(reactions=reactions, emoji_resolver=emoji_resolver)
        self._repo = adr_repository

    async def execute(
        self,
        message_ref: MessageRef,
        page_id: NotionPageId,
        scope_keys: list[str] | None = None,
    ) -> None:
        keys = tuple(scope_keys or [])
        # The same post can arrive twice (a Slack retry, then backfill), so
        # start from what was already put on it.
        tracked = await self._repo.get(page_id, message_ref) or TrackedADR(
            page_id=page_id, message_ref=message_ref
        )
        tracked = tracked.model_copy(update={"scope_keys": keys})

        try:
            snapshot = await self._source.fetch_snapshot(page_id)
        except Exception:
            # Most likely the Notion integration can't see the page. Track the
            # post anyway, so a later sync can react once it can.
            logger.warning("Failed to read ADR %s, not reacting", page_id, exc_info=True)
            await self._repo.save(tracked)
            return

        await self._repo.save(await self._update.execute(tracked, snapshot))
