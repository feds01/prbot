from datetime import UTC, datetime
from typing import override

from prbot.application.adr.handle_adr_message import HandleAdrMessage
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import AdrSnapshot, ConsensusRow, NotionPageId
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.value_objects import MessageRef
from tests.conftest import (
    FakeAdrRepository,
    FakeAdrSource,
    FakeEmojiConfigResolver,
    FakeReactions,
)

_PAGE = NotionPageId(value="31ed89c32a998009b89ee7e5d7bcf672")
_REF = MessageRef(integration_id="slack", ref="C1:1000.000001")
_SCOPES = ["slack/T1/C1", "slack/T1", "slack"]
_EDITED = datetime(2026, 11, 12, 9, 41, tzinfo=UTC)


def _snapshot(version: int, *rows: tuple[str, int, str]) -> AdrSnapshot:
    return AdrSnapshot(
        page_id=_PAGE,
        version=version,
        last_edited_time=_EDITED,
        rows=tuple(ConsensusRow(reviewer=r, version=v, comment=c) for r, v, c in rows),
    )


def _use_case(
    source: FakeAdrSource,
    repo: FakeAdrRepository,
    reactions: FakeReactions,
    config: EmojiConfig | None = None,
) -> HandleAdrMessage:
    return HandleAdrMessage(
        source=source,
        reactions=reactions,
        adr_repository=repo,
        emoji_resolver=FakeEmojiConfigResolver(config),
    )


class TestHandleAdrMessage:
    async def test_reacts_to_a_new_post_and_tracks_it(self) -> None:
        source = FakeAdrSource(_snapshot(2, ("ana", 2, "+1 LGTM"), ("ben", 2, "+0.5 why not X?")))
        repo, reactions = FakeAdrRepository(), FakeReactions()

        await _use_case(source, repo, reactions).execute(_REF, _PAGE, _SCOPES)

        assert [emoji for _, emoji in reactions.added] == ["two", "git-approved", "speech_balloon"]
        assert repo.stored[_PAGE, _REF] == TrackedADR(
            page_id=_PAGE,
            message_ref=_REF,
            applied_emojis=frozenset({"two", "git-approved", "speech_balloon"}),
            scope_keys=tuple(_SCOPES),
            last_edited_time=_EDITED,
        )

    async def test_uses_the_scopes_emoji(self) -> None:
        source = FakeAdrSource(_snapshot(1, ("ana", 1, "+1"), ("ben", 1, "-1")))
        reactions = FakeReactions()
        config = EmojiConfig(adr_contested="crossed_swords")

        await _use_case(source, FakeAdrRepository(), reactions, config).execute(_REF, _PAGE)

        assert [emoji for _, emoji in reactions.added] == ["one", "crossed_swords"]

    async def test_a_post_seen_again_is_brought_up_to_date(self) -> None:
        source = FakeAdrSource(_snapshot(3))
        seen = TrackedADR(
            page_id=_PAGE, message_ref=_REF, applied_emojis=frozenset({"two", "git-approved"})
        )
        repo, reactions = FakeAdrRepository(seen), FakeReactions()

        await _use_case(source, repo, reactions).execute(_REF, _PAGE, _SCOPES)

        assert {emoji for _, emoji, _ in reactions.removed} == {"two", "git-approved"}
        assert [emoji for _, emoji in reactions.added] == ["three"]
        assert repo.stored[_PAGE, _REF].applied_emojis == {"three"}

    async def test_an_unreadable_adr_is_tracked_without_reactions(self) -> None:
        repo, reactions = FakeAdrRepository(), FakeReactions()

        await _use_case(FakeAdrSource(), repo, reactions).execute(_REF, _PAGE, _SCOPES)

        assert reactions.added == []
        assert repo.stored[_PAGE, _REF] == TrackedADR(
            page_id=_PAGE, message_ref=_REF, scope_keys=tuple(_SCOPES)
        )

    async def test_an_unfinished_sync_leaves_the_edit_unseen(self) -> None:
        before = datetime(2026, 11, 1, tzinfo=UTC)
        seen = TrackedADR(
            page_id=_PAGE,
            message_ref=_REF,
            applied_emojis=frozenset({"two"}),
            last_edited_time=before,
        )
        repo = FakeAdrRepository(seen)

        await _use_case(FakeAdrSource(_snapshot(3)), repo, FailingRemovals()).execute(_REF, _PAGE)

        assert repo.stored[_PAGE, _REF].applied_emojis == {"two"}
        assert repo.stored[_PAGE, _REF].last_edited_time == before


class FailingRemovals(FakeReactions):
    @override
    async def remove_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        msg = "Slack is down"
        raise RuntimeError(msg)
