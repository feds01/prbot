import asyncio
import logging
from datetime import UTC, datetime, timedelta

from prbot.application.tracking.handle_github_webhook import HandleGitHubWebhook
from prbot.domain.tracking.ports import PRRepositoryPort, SourceRateLimitError
from prbot.domain.tracking.value_objects import PRUrl

logger = logging.getLogger(__name__)

# Matches the SQLite CURRENT_TIMESTAMP format the rows are written with.
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class ReconcileTrackedPRs:
    """Use case: on startup, re-check recently active PRs to catch missed events.

    Deliberately not "re-check everything". Each PR costs several API calls and
    the source enforces an hourly budget, so a full sweep of a long-lived
    database spends the whole allowance on PRs that were merged months ago and
    then runs dry before reaching the ones people are waiting on.
    """

    def __init__(
        self,
        pr_repository: PRRepositoryPort,
        handle_webhook: HandleGitHubWebhook,
        concurrency: int = 5,
        batch_size: int = 20,
        window_days: int | None = 7,
    ) -> None:
        self._repo = pr_repository
        self._handle_webhook = handle_webhook
        self._concurrency = concurrency
        self._batch_size = batch_size
        self._window_days = window_days

    def _cutoff(self) -> str | None:
        if self._window_days is None:
            return None
        since = datetime.now(UTC) - timedelta(days=self._window_days)
        return since.strftime(_TIMESTAMP_FORMAT)

    async def execute(self) -> None:
        cutoff = self._cutoff()
        pr_urls = await self._repo.find_distinct_pr_urls(since=cutoff)
        if not pr_urls:
            logger.info("Reconciliation: no tracked PRs found, nothing to do")
            return

        logger.info(
            "Reconciliation: checking %d tracked PRs active since %s",
            len(pr_urls),
            cutoff or "the beginning",
        )

        checked = 0
        skipped = 0
        stopped_early = False
        semaphore = asyncio.Semaphore(self._concurrency)

        for i in range(0, len(pr_urls), self._batch_size):
            batch = pr_urls[i : i + self._batch_size]
            results = await asyncio.gather(
                *(self._reconcile_one(semaphore, pr_url) for pr_url in batch),
            )
            checked += results.count(True)
            skipped += results.count(False)
            if results.count(None):
                stopped_early = True
                break

        if stopped_early:
            logger.warning(
                "Reconciliation stopped early — source rate limit reached after "
                "%d checked, %d skipped, %d of %d PRs never reached",
                checked,
                skipped,
                len(pr_urls) - checked - skipped,
                len(pr_urls),
            )
            return

        logger.info(
            "Reconciliation complete: %d checked, %d skipped out of %d PRs",
            checked,
            skipped,
            len(pr_urls),
        )

    async def _reconcile_one(self, semaphore: asyncio.Semaphore, pr_url: PRUrl) -> bool | None:
        """Reconcile one PR.

        Returns True if evaluated, False if skipped, None if the budget ran out.
        """
        async with semaphore:
            logger.debug("Reconciling %s", pr_url)
            try:
                return await self._handle_webhook.execute(
                    owner=pr_url.owner,
                    repo=pr_url.repo,
                    number=pr_url.number,
                )
            except SourceRateLimitError:
                return None
            except Exception:
                logger.warning("Reconciliation failed for %s", pr_url, exc_info=True)
                return False
