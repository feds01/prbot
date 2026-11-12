from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prbot.data.database import Base
from prbot.data.repository import SQLiteAdrRepository
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.tracking.value_objects import MessageRef

_PAGE = NotionPageId(value="31ed89c32a998009b89ee7e5d7bcf672")
_OTHER_PAGE = NotionPageId(value="0123456789abcdef0123456789abcdef")
_REF = MessageRef(integration_id="slack", ref="C1:1000.000001")
_EDITED = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
async def repository() -> AsyncIterator[SQLiteAdrRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield SQLiteAdrRepository(session_factory=async_sessionmaker[AsyncSession](engine))
    await engine.dispose()


def _tracked(**changes: object) -> TrackedADR:
    tracked = TrackedADR(
        page_id=_PAGE,
        message_ref=_REF,
        applied_emojis=frozenset({"two", "git-approved"}),
        scope_keys=("slack/T1/C1", "slack/T1", "slack"),
        last_edited_time=_EDITED,
    )
    return tracked.model_copy(update=changes)


class TestSQLiteAdrRepository:
    async def test_saved_post_reads_back_unchanged(self, repository: SQLiteAdrRepository) -> None:
        await repository.save(_tracked())
        assert await repository.get(_PAGE, _REF) == _tracked()

    async def test_unknown_post_is_none(self, repository: SQLiteAdrRepository) -> None:
        assert await repository.get(_PAGE, _REF) is None

    async def test_saving_again_replaces_the_stored_post(
        self, repository: SQLiteAdrRepository
    ) -> None:
        await repository.save(_tracked())
        later = datetime(2026, 11, 2, tzinfo=UTC)
        await repository.save(_tracked(applied_emojis=frozenset({"three"}), last_edited_time=later))

        stored = await repository.get(_PAGE, _REF)
        assert stored is not None
        assert stored.applied_emojis == frozenset({"three"})
        assert stored.last_edited_time == later

    async def test_empty_post_reads_back_empty(self, repository: SQLiteAdrRepository) -> None:
        empty = _tracked(applied_emojis=frozenset(), scope_keys=(), last_edited_time=None)
        await repository.save(empty)
        assert await repository.get(_PAGE, _REF) == empty

    async def test_posts_are_kept_per_page_and_message(
        self, repository: SQLiteAdrRepository
    ) -> None:
        other_message = MessageRef(integration_id="slack", ref="C2:2000.000001")
        await repository.save(_tracked())
        await repository.save(_tracked(page_id=_OTHER_PAGE, applied_emojis=frozenset({"one"})))
        await repository.save(_tracked(message_ref=other_message, applied_emojis=frozenset()))

        mine = await repository.get(_PAGE, _REF)
        assert mine is not None
        assert mine.applied_emojis == frozenset({"two", "git-approved"})
        assert await repository.get(_OTHER_PAGE, other_message) is None
