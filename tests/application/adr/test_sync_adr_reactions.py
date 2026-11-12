from typing import override

from prbot.application.adr.sync_adr_reactions import SyncAdrReactions
from prbot.domain.adr.consensus import AdrEmoji
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.value_objects import MessageRef
from tests.conftest import FakeReactions

_REF = MessageRef(integration_id="slack", ref="C1:1000.000001")
_CONFIG = EmojiConfig()
_TWO = AdrEmoji(emoji="two", fallback="2\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}")
_THREE = AdrEmoji(
    emoji="three", fallback="3\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}"
)
_APPROVED = AdrEmoji(emoji="git-approved", fallback="\N{WHITE HEAVY CHECK MARK}")
_CONTESTED = AdrEmoji(emoji="boxing_glove", fallback="\N{BOXING GLOVE}")


def _tracked(*applied: str) -> TrackedADR:
    return TrackedADR(
        page_id=NotionPageId(value="31ed89c32a998009b89ee7e5d7bcf672"),
        message_ref=_REF,
        applied_emojis=frozenset(applied),
    )


class OrderedReactions(FakeReactions):
    """Records adds and removes in one list, to check their order."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    @override
    async def add_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        await super().add_reaction(message_ref, emoji, fallback_emoji)
        self.calls.append(f"+{emoji}")

    @override
    async def remove_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        await super().remove_reaction(message_ref, emoji, fallback_emoji)
        self.calls.append(f"-{emoji}")


class FailingRemovals(OrderedReactions):
    @override
    async def remove_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        msg = "Slack is down"
        raise RuntimeError(msg)


class TestSyncAdrReactions:
    async def test_adds_what_a_new_post_should_show(self) -> None:
        reactions = OrderedReactions()
        synced = await SyncAdrReactions(reactions).execute(_tracked(), [_TWO, _APPROVED], _CONFIG)

        assert reactions.calls == ["+two", "+git-approved"]
        assert reactions.added_with_fallback == [
            (_REF, "two", _TWO.fallback),
            (_REF, "git-approved", _APPROVED.fallback),
        ]
        assert synced.applied_emojis == {"two", "git-approved"}

    async def test_removes_stale_emoji_before_adding(self) -> None:
        reactions = OrderedReactions()
        synced = await SyncAdrReactions(reactions).execute(
            _tracked("two", "git-approved"), [_TWO, _CONTESTED], _CONFIG
        )

        assert reactions.calls == ["-git-approved", "+boxing_glove"]
        assert synced.applied_emojis == {"two", "boxing_glove"}

    async def test_new_version_clears_everything_else(self) -> None:
        reactions = OrderedReactions()
        synced = await SyncAdrReactions(reactions).execute(
            _tracked("two", "boxing_glove", "speech_balloon"), [_THREE], _CONFIG
        )

        assert reactions.calls == ["-boxing_glove", "-speech_balloon", "-two", "+three"]
        assert synced.applied_emojis == {"three"}

    async def test_removal_clears_the_fallback_too(self) -> None:
        reactions = FakeReactions()
        await SyncAdrReactions(reactions).execute(_tracked("two"), [_THREE], _CONFIG)
        assert reactions.removed == [(_REF, "two", _TWO.fallback)]

    async def test_running_twice_changes_nothing_the_second_time(self) -> None:
        reactions = OrderedReactions()
        sync = SyncAdrReactions(reactions)
        synced = await sync.execute(_tracked("one"), [_TWO, _APPROVED], _CONFIG)
        reactions.calls.clear()

        again = await sync.execute(synced, [_TWO, _APPROVED], _CONFIG)

        assert reactions.calls == []
        assert again == synced

    async def test_adds_nothing_when_a_removal_fails(self) -> None:
        reactions = FailingRemovals()
        synced = await SyncAdrReactions(reactions).execute(
            _tracked("two", "git-approved"), [_TWO, _CONTESTED], _CONFIG
        )

        assert reactions.calls == []
        assert synced.applied_emojis == {"two", "git-approved"}
