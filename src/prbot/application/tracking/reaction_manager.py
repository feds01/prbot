import logging
from dataclasses import dataclass

from prbot.application.tracking.ci_emoji import ci_emoji_to_add
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.ports import ReactionPort
from prbot.domain.tracking.status_resolver import resolve_re_review_requested
from prbot.domain.tracking.value_objects import MessageRef, PRInfo, PRStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reaction:
    """A single emoji reaction to add or remove, with an optional fallback."""

    emoji: str
    fallback: str | None


class ReactionManager:
    """Decides which emoji reactions a PR warrants, and applies them idempotently.

    Three independent tracks feed a plan:

    - the review-status emoji — the single, mutually-exclusive outcome of the
      review state (approved / changes-requested / commented / merged / …);
    - the CI-failure emoji — derived from check-runs, applied *alongside* the
      review emoji when CI is failing;
    - the re-review emoji — applied *alongside* the review emoji when review
      is re-requested from someone who has already reviewed the PR, and the
      only emoji ever taken back: ``plan_removals`` clears it once nobody is
      waiting to re-review.

    ``plan`` is pure and skips any emoji already on the message, and any
    duplicate within the same batch, so applying the result is idempotent.
    Persistence is left to the caller, since the webhook (already-tracked PR)
    and incoming-message (newly-tracked PR) flows record emoji differently.
    """

    def __init__(self, reactions: ReactionPort) -> None:
        self._reactions = reactions

    @staticmethod
    def plan(
        config: EmojiConfig,
        status: PRStatus,
        pr_info: PRInfo,
        tracked: TrackedPR,
    ) -> list[Reaction]:
        """Compute the reactions to add for a PR, skipping any already present."""
        reactions: list[Reaction] = []
        queued: set[str] = set()

        def _want(emoji: str | None, fallback: str | None) -> None:
            if emoji is None or emoji in queued or tracked.has_emoji(emoji):
                return
            queued.add(emoji)
            reactions.append(Reaction(emoji, fallback))

        _want(config.for_status(status), EmojiConfig.fallback_for_status(status))

        ci = ci_emoji_to_add(config, pr_info, tracked)
        if ci is not None:
            _want(ci[0], ci[1])

        if resolve_re_review_requested(pr_info):
            _want(
                config.for_status(PRStatus.RE_REVIEW_REQUESTED),
                EmojiConfig.fallback_for_status(PRStatus.RE_REVIEW_REQUESTED),
            )

        return reactions

    @staticmethod
    def plan_removals(config: EmojiConfig, pr_info: PRInfo, tracked: TrackedPR) -> list[Reaction]:
        """Compute the reactions to take back now that the PR no longer warrants them."""
        status = PRStatus.RE_REVIEW_REQUESTED
        emoji = config.for_status(status)
        if emoji is None or not tracked.has_emoji(emoji) or resolve_re_review_requested(pr_info):
            return []
        # A workspace could map another status to the same name; removing it
        # would strip that status's reaction too, so leave it in place.
        if any(config.for_status(other) == emoji for other in PRStatus if other != status):
            return []
        return [Reaction(emoji, EmojiConfig.fallback_for_status(status))]

    async def apply(self, message_ref: MessageRef, reactions: list[Reaction]) -> list[str]:
        """Add each planned reaction to the message; return the emojis actually added.

        A message that cannot be reacted to (deleted, or otherwise unreachable)
        must not starve the other messages tracking the same PR, so a failure
        stops this message's batch and reports what landed rather than raising.
        """
        added: list[str] = []
        for reaction in reactions:
            try:
                await self._reactions.add_reaction(message_ref, reaction.emoji, reaction.fallback)
            except Exception:
                logger.warning(
                    "Failed to react with %r to %s, skipping",
                    reaction.emoji,
                    message_ref,
                    exc_info=True,
                )
                break
            added.append(reaction.emoji)
        return added

    async def remove(self, message_ref: MessageRef, reactions: list[Reaction]) -> list[str]:
        """Remove each planned reaction from the message; return the emojis actually removed.

        Fails per message the same way ``apply`` does.
        """
        removed: list[str] = []
        for reaction in reactions:
            try:
                await self._reactions.remove_reaction(
                    message_ref, reaction.emoji, reaction.fallback
                )
            except Exception:
                logger.warning(
                    "Failed to remove %r from %s, skipping",
                    reaction.emoji,
                    message_ref,
                    exc_info=True,
                )
                break
            removed.append(reaction.emoji)
        return removed
