import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from prbot.application.adr.update_adr_post import UpdateAdrPost
from prbot.domain.adr.entities import TrackedADR
from prbot.domain.adr.ports import AdrRepositoryPort, AdrSourcePort
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.emoji.ports import EmojiConfigResolverPort
from prbot.domain.tracking.ports import ReactionPort

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class RefreshTrackedADRs:
    """Use case: keep recent ADR review posts in step with their ADRs.

    Notion doesn't tell the bot when an ADR changes, so each pass re-reads the
    ADR behind every post that changed in the last ``window_days``: once per
    ADR, however many posts link it. A post already up to date costs no
    reactions and no write, so it drops out once its ADR has been quiet for
    the whole window.

    Each page is read in full rather than first checking its
    ``last_edited_time``. Notion doesn't document whether editing a table cell
    changes the page's ``last_edited_time``, and if it doesn't, that shortcut
    would miss every new review.
    """

    def __init__(  # noqa: PLR0913 - constructor injection: each collaborator is a named port
        self,
        *,
        source: AdrSourcePort,
        adr_repository: AdrRepositoryPort,
        reactions: ReactionPort,
        emoji_resolver: EmojiConfigResolverPort,
        interval_seconds: float = 120,
        window_days: int = 30,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source = source
        self._repo = adr_repository
        self._update = UpdateAdrPost(reactions=reactions, emoji_resolver=emoji_resolver)
        self._interval = interval_seconds
        self._window = timedelta(days=window_days)
        self._clock = clock
        self._sleep = sleep

    async def run_forever(self) -> None:
        """Refresh now, then again every *interval_seconds* until cancelled."""
        while True:
            try:
                await self.execute()
            except Exception:
                logger.exception("ADR refresh failed")
            await self._sleep(self._interval)

    async def execute(self) -> None:
        """Refresh every post that changed within the window, one ADR at a time."""
        posts_by_page: defaultdict[NotionPageId, list[TrackedADR]] = defaultdict(list)
        for post in await self._repo.find_changed_since(self._clock() - self._window):
            posts_by_page[post.page_id].append(post)
        # One ADR at a time; the gateway spaces out the requests within each.
        for page_id, posts in posts_by_page.items():
            await self._refresh_page(page_id, posts)

    async def _refresh_page(self, page_id: NotionPageId, posts: list[TrackedADR]) -> None:
        try:
            snapshot = await self._source.fetch_snapshot(page_id)
        except Exception as exc:  # noqa: BLE001 - logged below; the other ADRs go on
            # Usually the integration can't see the page, which repeats every
            # pass until someone connects it. The gateway has already logged
            # Notion's response, so this stays one line rather than a traceback.
            logger.warning(
                "Failed to read ADR %s, %d posts left as they are: %r", page_id, len(posts), exc
            )
            return
        for listed in posts:
            try:
                # Re-read the post: a pass can take minutes, and a Slack retry
                # may have saved it since the pass started.
                post = await self._repo.get(listed.page_id, listed.message_ref) or listed
                updated = await self._update.execute(post, snapshot)
                if updated != post:
                    await self._repo.save(updated)
            except Exception:
                logger.warning(
                    "Failed to refresh post %s for ADR %s",
                    listed.message_ref,
                    page_id,
                    exc_info=True,
                )
