from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prbot.data.database import Base, TrackedADRRow
from prbot.data.repository import SQLiteAdrRepository
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.tracking.value_objects import MessageRef

_PAGE = NotionPageId(value="31ed89c32a998009b89ee7e5d7bcf672")
_OTHER_PAGE = NotionPageId(value="0123456789abcdef0123456789abcdef")
_REF = MessageRef(integration_id="slack", ref="C1:1000.000001")
_EDITED = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker[AsyncSession](engine)
    await engine.dispose()


@pytest.fixture
def repository(session_factory: async_sessionmaker[AsyncSession]) -> SQLiteAdrRepository:
    return SQLiteAdrRepository(session_factory=session_factory)


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


_MONTH_AGO = "2026-09-01 12:00:00"


async def _age(session_factory: async_sessionmaker[AsyncSession], ref: MessageRef) -> None:
    """Make a stored post look last saved two months ago."""
    async with session_factory() as session:
        await session.execute(
            update(TrackedADRRow)
            .where(TrackedADRRow.message_ref == ref.ref)
            .values(created_at=_MONTH_AGO, updated_at=_MONTH_AGO)
        )
        await session.commit()


class TestFindChangedSince:
    async def test_returns_posts_saved_since_the_cutoff(
        self,
        repository: SQLiteAdrRepository,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        old_message = MessageRef(integration_id="slack", ref="C1:900.000001")
        await repository.save(_tracked())
        await repository.save(_tracked(message_ref=old_message))
        await _age(session_factory, old_message)

        found = await repository.find_changed_since(datetime.now(UTC) - timedelta(days=30))

        assert found == [_tracked()]

    async def test_saving_an_old_post_brings_it_back(
        self,
        repository: SQLiteAdrRepository,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await repository.save(_tracked())
        await _age(session_factory, _REF)
        await repository.save(_tracked(applied_emojis=frozenset({"three"})))

        found = await repository.find_changed_since(datetime.now(UTC) - timedelta(days=30))

        assert found == [_tracked(applied_emojis=frozenset({"three"}))]

    async def test_compares_in_utc(self, repository: SQLiteAdrRepository) -> None:
        await repository.save(_tracked())
        # An hour from now, written in a zone 5 hours behind UTC.
        later = (datetime.now(UTC) + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-5)))

        assert await repository.find_changed_since(later) == []
