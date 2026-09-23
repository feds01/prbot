from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prbot.data.database import Base
from prbot.data.repository import SQLiteChannelCursorRepository, SQLitePRRepository
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.value_objects import MessageRef, PRUrl


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    # Without this the connection is only closed by the garbage collector,
    # which raises out of `Connection.__del__` after the loop has gone.
    await engine.dispose()


@pytest.fixture
async def repository(session_factory: async_sessionmaker) -> SQLitePRRepository:
    return SQLitePRRepository(session_factory=session_factory)


@pytest.fixture
async def cursor_repository(session_factory: async_sessionmaker) -> SQLiteChannelCursorRepository:
    return SQLiteChannelCursorRepository(session_factory=session_factory)


def _pr_url(number: int = 1) -> PRUrl:
    return PRUrl(owner="octocat", repo="hello", number=number)


def _msg_ref(channel: str = "C123", ts: str = "1234.5678") -> MessageRef:
    return MessageRef(integration_id="slack", ref=f"{channel}:{ts}")


def _tracked(
    number: int = 1,
    channel: str = "C123",
    ts: str = "1234.5678",
    emojis: frozenset[str] = frozenset(),
) -> TrackedPR:
    return TrackedPR(
        pr_url=_pr_url(number),
        message_ref=_msg_ref(channel, ts),
        applied_emojis=emojis,
    )


class TestSQLitePRRepository:
    async def test_save_and_find(self, repository: SQLitePRRepository) -> None:
        tracked = _tracked(emojis=frozenset({"eyes"}))
        await repository.save(tracked)

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 1
        assert results[0].pr_url == tracked.pr_url
        assert results[0].message_ref == _msg_ref()
        assert results[0].applied_emojis == frozenset({"eyes"})

    async def test_upsert_on_conflict(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(emojis=frozenset({"eyes"})))
        await repository.save(_tracked(emojis=frozenset({"eyes", "white_check_mark"})))

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 1
        assert results[0].applied_emojis == frozenset({"eyes", "white_check_mark"})

    async def test_add_emoji(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(emojis=frozenset({"eyes"})))

        await repository.add_emoji(_pr_url(), _msg_ref(), "tada")

        results = await repository.find_by_pr_url(_pr_url())
        assert results[0].applied_emojis == frozenset({"eyes", "tada"})

    async def test_find_returns_empty_for_unknown(self, repository: SQLitePRRepository) -> None:
        results = await repository.find_by_pr_url(_pr_url(999))
        assert len(results) == 0

    async def test_multiple_messages_for_same_pr(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(channel="C1", ts="1.0"))
        await repository.save(_tracked(channel="C2", ts="2.0"))
        await repository.save(_tracked(channel="C1", ts="3.0"))

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 3

    async def test_find_distinct_pr_urls_empty(self, repository: SQLitePRRepository) -> None:
        results = await repository.find_distinct_pr_urls()
        assert len(results) == 0

    async def test_find_distinct_pr_urls_deduplicates(self, repository: SQLitePRRepository) -> None:
        # Same PR tracked across multiple messages
        await repository.save(_tracked(number=1, channel="C1", ts="1.0"))
        await repository.save(_tracked(number=1, channel="C2", ts="2.0"))
        await repository.save(_tracked(number=2, channel="C3", ts="3.0"))

        results = await repository.find_distinct_pr_urls()
        assert len(results) == 2
        numbers = {r.number for r in results}
        assert numbers == {1, 2}

    async def test_save_with_no_emojis(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked())

        results = await repository.find_by_pr_url(_pr_url())
        assert results[0].applied_emojis == frozenset()


class TestSQLiteChannelCursorRepository:
    async def test_get_cursor_returns_none_when_missing(
        self, cursor_repository: SQLiteChannelCursorRepository
    ) -> None:
        result = await cursor_repository.get_cursor("slack", "C123")
        assert result is None

    async def test_upsert_and_get(self, cursor_repository: SQLiteChannelCursorRepository) -> None:
        await cursor_repository.upsert_cursor("slack", "C123", "1000.000000")
        result = await cursor_repository.get_cursor("slack", "C123")
        assert result == "1000.000000"

    async def test_upsert_advances_forward(
        self, cursor_repository: SQLiteChannelCursorRepository
    ) -> None:
        await cursor_repository.upsert_cursor("slack", "C123", "1000.000000")
        await cursor_repository.upsert_cursor("slack", "C123", "2000.000000")
        result = await cursor_repository.get_cursor("slack", "C123")
        assert result == "2000.000000"

    async def test_upsert_overwrites(
        self, cursor_repository: SQLiteChannelCursorRepository
    ) -> None:
        await cursor_repository.upsert_cursor("slack", "C123", "2000.000000")
        await cursor_repository.upsert_cursor("slack", "C123", "1000.000000")
        result = await cursor_repository.get_cursor("slack", "C123")
        assert result == "1000.000000"

    async def test_separate_channels_are_independent(
        self, cursor_repository: SQLiteChannelCursorRepository
    ) -> None:
        await cursor_repository.upsert_cursor("slack", "C1", "1000.000000")
        await cursor_repository.upsert_cursor("slack", "C2", "2000.000000")

        assert await cursor_repository.get_cursor("slack", "C1") == "1000.000000"
        assert await cursor_repository.get_cursor("slack", "C2") == "2000.000000"

    async def test_seed_from_tracked_prs(
        self,
        session_factory: async_sessionmaker,
        cursor_repository: SQLiteChannelCursorRepository,
    ) -> None:
        # Insert some tracked PRs with message_refs in channel:ts format
        pr_repo = SQLitePRRepository(session_factory=session_factory)
        await pr_repo.save(_tracked(number=1, channel="C1", ts="1000.000000"))
        await pr_repo.save(_tracked(number=2, channel="C1", ts="2000.000000"))
        await pr_repo.save(_tracked(number=3, channel="C2", ts="1500.000000"))

        seeded = await cursor_repository.seed_missing_from_tracked_prs("slack")
        assert seeded == 2

        # Should have picked the latest ts per channel
        assert await cursor_repository.get_cursor("slack", "C1") == "2000.000000"
        assert await cursor_repository.get_cursor("slack", "C2") == "1500.000000"

    async def test_seed_skips_channels_with_existing_cursor(
        self,
        session_factory: async_sessionmaker,
        cursor_repository: SQLiteChannelCursorRepository,
    ) -> None:
        pr_repo = SQLitePRRepository(session_factory=session_factory)
        await pr_repo.save(_tracked(number=1, channel="C1", ts="1000.000000"))

        # Pre-set a cursor for C1
        await cursor_repository.upsert_cursor("slack", "C1", "500.000000")

        seeded = await cursor_repository.seed_missing_from_tracked_prs("slack")
        assert seeded == 0

        # Original cursor should be unchanged
        assert await cursor_repository.get_cursor("slack", "C1") == "500.000000"


async def _set_activity(session_factory: async_sessionmaker, number: int, stamp: str) -> None:
    """Rewrite a row's server-set updated_at so ordering can be asserted."""
    async with session_factory() as session:
        await session.execute(
            text("UPDATE tracked_prs SET updated_at = :stamp WHERE pr_number = :number"),
            {"stamp": stamp, "number": number},
        )
        await session.commit()


class TestDistinctPRUrlOrdering:
    async def test_orders_by_most_recent_activity(
        self, repository: SQLitePRRepository, session_factory: async_sessionmaker
    ) -> None:
        await repository.save(_tracked(number=1, channel="C1", ts="1.0"))
        await repository.save(_tracked(number=2, channel="C2", ts="2.0"))
        await repository.save(_tracked(number=3, channel="C3", ts="3.0"))
        await _set_activity(session_factory, 1, "2020-01-01 00:00:00")
        await _set_activity(session_factory, 2, "2026-09-18 00:00:00")
        await _set_activity(session_factory, 3, "2024-06-01 00:00:00")

        results = await repository.find_distinct_pr_urls()

        assert [r.number for r in results] == [2, 3, 1]

    async def test_since_excludes_stale_prs(
        self, repository: SQLitePRRepository, session_factory: async_sessionmaker
    ) -> None:
        await repository.save(_tracked(number=1, channel="C1", ts="1.0"))
        await repository.save(_tracked(number=2, channel="C2", ts="2.0"))
        await _set_activity(session_factory, 1, "2020-01-01 00:00:00")
        await _set_activity(session_factory, 2, "2026-09-18 00:00:00")

        results = await repository.find_distinct_pr_urls(since="2026-09-01 00:00:00")

        assert [r.number for r in results] == [2]

    async def test_a_pr_is_as_recent_as_its_newest_message(
        self, repository: SQLitePRRepository, session_factory: async_sessionmaker
    ) -> None:
        # One PR tracked twice: the stale copy must not drag it out of the window.
        await repository.save(_tracked(number=1, channel="C1", ts="1.0"))
        await repository.save(_tracked(number=1, channel="C2", ts="2.0"))
        async with session_factory() as session:
            await session.execute(
                text("UPDATE tracked_prs SET updated_at = :s WHERE message_ref LIKE :ref"),
                {"s": "2020-01-01 00:00:00", "ref": "C1%"},
            )
            await session.execute(
                text("UPDATE tracked_prs SET updated_at = :s WHERE message_ref LIKE :ref"),
                {"s": "2026-09-18 00:00:00", "ref": "C2%"},
            )
            await session.commit()

        results = await repository.find_distinct_pr_urls(since="2026-09-01 00:00:00")

        assert [r.number for r in results] == [1]
