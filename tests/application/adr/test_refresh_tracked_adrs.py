import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import override

import pytest

from prbot.application.adr.refresh_tracked_adrs import RefreshTrackedADRs
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import AdrSnapshot, ConsensusRow, NotionPageId
from prbot.domain.tracking.value_objects import MessageRef
from tests.conftest import (
    FakeAdrRepository,
    FakeAdrSource,
    FakeEmojiConfigResolver,
    FakeReactions,
)

_PAGE = NotionPageId(value="31ed89c32a998009b89ee7e5d7bcf672")
_OTHER_PAGE = NotionPageId(value="0123456789abcdef0123456789abcdef")
_REF = MessageRef(integration_id="slack", ref="C1:1000.000001")
_OTHER_REF = MessageRef(integration_id="slack", ref="C2:2000.000001")
_NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
_EDITED = datetime(2026, 11, 12, 11, 30, tzinfo=UTC)


def _snapshot(
    version: int, *rows: tuple[str, int, str], page_id: NotionPageId = _PAGE
) -> AdrSnapshot:
    return AdrSnapshot(
        page_id=page_id,
        version=version,
        last_edited_time=_EDITED,
        rows=tuple(ConsensusRow(reviewer=r, version=v, comment=c) for r, v, c in rows),
    )


def _post(
    *applied: str,
    page_id: NotionPageId = _PAGE,
    ref: MessageRef = _REF,
    edited: datetime | None = None,
) -> TrackedADR:
    return TrackedADR(
        page_id=page_id,
        message_ref=ref,
        applied_emojis=frozenset(applied),
        last_edited_time=edited,
    )


def _use_case(
    source: FakeAdrSource, repo: FakeAdrRepository, reactions: FakeReactions
) -> RefreshTrackedADRs:
    return RefreshTrackedADRs(
        source=source,
        adr_repository=repo,
        reactions=reactions,
        emoji_resolver=FakeEmojiConfigResolver(),
        clock=lambda: _NOW,
    )


class TestRefreshTrackedADRs:
    async def test_a_new_version_clears_the_old_versions_emoji(self) -> None:
        repo = FakeAdrRepository(_post("two", "git-approved", edited=_EDITED - timedelta(days=1)))
        reactions = FakeReactions()

        await _use_case(FakeAdrSource(_snapshot(3)), repo, reactions).execute()

        assert {emoji for _, emoji, _ in reactions.removed} == {"two", "git-approved"}
        assert [emoji for _, emoji in reactions.added] == ["three"]
        assert repo.stored[_PAGE, _REF] == _post("three", edited=_EDITED)

    async def test_a_new_review_adds_its_emoji(self) -> None:
        repo = FakeAdrRepository(_post("two", edited=_EDITED - timedelta(minutes=5)))
        reactions = FakeReactions()
        source = FakeAdrSource(_snapshot(2, ("ana", 2, "-1 needs a rollback plan")))

        await _use_case(source, repo, reactions).execute()

        assert [emoji for _, emoji in reactions.added] == [
            "git-changes-requested",
            "speech_balloon",
        ]

    async def test_an_up_to_date_post_is_left_alone(self) -> None:
        current = _post("two", edited=_EDITED)
        repo, reactions = UnwritableAdrRepository(current), FakeReactions()

        await _use_case(FakeAdrSource(_snapshot(2)), repo, reactions).execute()

        assert reactions.added == []
        assert reactions.removed == []

    async def test_a_post_that_never_got_reactions_gets_them(self) -> None:
        repo, reactions = FakeAdrRepository(_post()), FakeReactions()

        await _use_case(FakeAdrSource(_snapshot(1)), repo, reactions).execute()

        assert [emoji for _, emoji in reactions.added] == ["one"]
        assert repo.stored[_PAGE, _REF] == _post("one", edited=_EDITED)

    async def test_reads_each_adr_once_for_all_its_posts(self) -> None:
        repo = FakeAdrRepository(_post(), _post(ref=_OTHER_REF))
        source, reactions = FakeAdrSource(_snapshot(1)), FakeReactions()

        await _use_case(source, repo, reactions).execute()

        assert source.fetched == [_PAGE]
        assert sorted(ref.ref for ref, _ in reactions.added) == [_REF.ref, _OTHER_REF.ref]

    async def test_an_unreadable_adr_does_not_stop_the_others(self) -> None:
        repo = FakeAdrRepository(_post(page_id=_OTHER_PAGE), _post(ref=_OTHER_REF))
        source, reactions = FakeAdrSource(_snapshot(1)), FakeReactions()

        await _use_case(source, repo, reactions).execute()

        assert source.fetched == [_OTHER_PAGE, _PAGE]
        assert repo.stored[_OTHER_PAGE, _REF] == _post(page_id=_OTHER_PAGE)
        assert repo.stored[_PAGE, _OTHER_REF].applied_emojis == {"one"}

    async def test_a_failing_post_does_not_stop_the_others(self) -> None:
        repo = UnsavableFor(_REF, _post(), _post(ref=_OTHER_REF))

        await _use_case(FakeAdrSource(_snapshot(1)), repo, FakeReactions()).execute()

        assert repo.stored[_PAGE, _REF] == _post()
        assert repo.stored[_PAGE, _OTHER_REF].applied_emojis == {"one"}

    async def test_starts_from_the_post_as_saved_now(self) -> None:
        listed = _post("two", edited=_EDITED - timedelta(days=1))
        # Saved by a Slack retry after the pass listed the post.
        saved_since = _post("three", edited=_EDITED)
        repo = ListsStale([listed], saved_since)
        reactions = FakeReactions()

        await _use_case(FakeAdrSource(_snapshot(3)), repo, reactions).execute()

        assert reactions.removed == []
        assert reactions.added == []

    async def test_looks_back_over_the_window(self) -> None:
        repo = FakeAdrRepository()

        await _use_case(FakeAdrSource(), repo, FakeReactions()).execute()

        assert repo.since == _NOW - timedelta(days=30)

    async def test_a_quiet_adr_drops_out(self) -> None:
        """Nothing changed, so nothing is saved and the window moves past the post."""
        repo = UnwritableAdrRepository(_post("one", edited=_EDITED))

        await _use_case(FakeAdrSource(_snapshot(1)), repo, FakeReactions()).execute()


class TestRunForever:
    async def test_keeps_going_after_a_failed_pass_until_cancelled(self) -> None:
        passes: list[None] = []
        intervals: list[float] = []

        class Refresh(RefreshTrackedADRs):
            @override
            async def execute(self) -> None:
                passes.append(None)
                if len(passes) == 1:
                    msg = "database is locked"
                    raise RuntimeError(msg)

        async def sleep(seconds: float) -> None:
            intervals.append(seconds)
            if len(intervals) == 3:
                raise asyncio.CancelledError

        refresh = Refresh(
            source=FakeAdrSource(),
            adr_repository=FakeAdrRepository(),
            reactions=FakeReactions(),
            emoji_resolver=FakeEmojiConfigResolver(),
            interval_seconds=120,
            sleep=sleep,
        )

        with pytest.raises(asyncio.CancelledError):
            await refresh.run_forever()

        assert len(passes) == 3
        assert intervals == [120, 120, 120]


class UnwritableAdrRepository(FakeAdrRepository):
    @override
    async def save(self, tracked: TrackedADR) -> None:
        msg = "an up-to-date post should not be written"
        raise AssertionError(msg)


class UnsavableFor(FakeAdrRepository):
    def __init__(self, ref: MessageRef, *tracked: TrackedADR) -> None:
        super().__init__(*tracked)
        self._ref = ref

    @override
    async def save(self, tracked: TrackedADR) -> None:
        if tracked.message_ref == self._ref:
            msg = "database is locked"
            raise RuntimeError(msg)
        await super().save(tracked)


class ListsStale(FakeAdrRepository):
    """Lists posts as they were when the pass started; get returns them as saved now."""

    def __init__(self, listed: list[TrackedADR], *tracked: TrackedADR) -> None:
        super().__init__(*tracked)
        self._listed = listed

    @override
    async def find_changed_since(self, since: datetime) -> Sequence[TrackedADR]:
        return self._listed
