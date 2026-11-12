from prbot.application.adr.sync_adr_reactions import SyncAdrReactions
from prbot.domain.adr.consensus import evaluate_consensus, wanted_emojis
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import AdrSnapshot
from prbot.domain.emoji.ports import EmojiConfigResolverPort
from prbot.domain.tracking.ports import ReactionPort


class UpdateAdrPost:
    """Bring one ADR review post in line with what its ADR says now.

    Returns the post as it now stands; saving it is up to the caller.
    """

    def __init__(
        self,
        *,
        reactions: ReactionPort,
        emoji_resolver: EmojiConfigResolverPort,
    ) -> None:
        self._sync = SyncAdrReactions(reactions)
        self._emoji_resolver = emoji_resolver

    async def execute(self, tracked: TrackedADR, snapshot: AdrSnapshot) -> TrackedADR:
        config = await self._emoji_resolver.resolve(list(tracked.scope_keys))
        wanted = wanted_emojis(evaluate_consensus(snapshot), config)
        tracked = await self._sync.execute(tracked, wanted, config)
        # Record the edit as seen only once the post shows it, so a sync that
        # stopped early is retried rather than skipped as up to date.
        if tracked.applied_emojis == {e.emoji for e in wanted}:
            tracked = tracked.model_copy(update={"last_edited_time": snapshot.last_edited_time})
        return tracked
