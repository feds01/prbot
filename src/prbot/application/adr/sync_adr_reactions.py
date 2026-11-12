from collections.abc import Sequence

from prbot.application.tracking.reaction_manager import Reaction, ReactionManager
from prbot.domain.adr.consensus import AdrEmoji, fallback_for
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.ports import ReactionPort


class SyncAdrReactions:
    """Make an ADR post's reactions match the emoji its ADR calls for.

    Unlike PR posts, which only gain emoji, an ADR post loses the ones its ADR
    no longer calls for: a new version clears the old version's emoji, and
    approved gives way to 🥊. Stale emoji come off before new ones go on, so
    approved and changes-requested are never on the post together.
    """

    def __init__(self, reactions: ReactionPort) -> None:
        self._reactions = ReactionManager(reactions)

    async def execute(
        self,
        tracked: TrackedADR,
        wanted: Sequence[AdrEmoji],
        config: EmojiConfig,
    ) -> TrackedADR:
        """Add and remove reactions on the post; return it with what is now applied."""
        wanted_names = {e.emoji for e in wanted}
        stale = [
            Reaction(emoji, fallback_for(emoji, config))
            for emoji in sorted(tracked.applied_emojis - wanted_names)
        ]
        removed = await self._reactions.remove(tracked.message_ref, stale)
        applied = tracked.applied_emojis - set(removed)
        if len(removed) < len(stale):
            # Adding now could put approved next to a changes-requested that
            # didn't come off. Leave it for the next sync.
            return tracked.model_copy(update={"applied_emojis": applied})

        missing = [Reaction(e.emoji, e.fallback) for e in wanted if e.emoji not in applied]
        added = await self._reactions.apply(tracked.message_ref, missing)
        return tracked.model_copy(update={"applied_emojis": applied | set(added)})
