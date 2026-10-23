from collections.abc import Sequence

from prbot.domain.tracking.value_objects import CheckRun, PRInfo, PRStatus, Review, ReviewState

# check-run conclusions that count as a CI failure.
_FAILING_CI_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})
# Completed, but CI never actually ran to a verdict — neither a pass nor a failure.
_INCONCLUSIVE_CI_CONCLUSIONS = frozenset({"cancelled", "stale"})


def resolve_ci_failing(check_runs: tuple[CheckRun, ...] | None) -> bool | None:
    """Derive the aggregate CI state for a commit from its check-runs.

    Tri-state: ``True`` if any run failed, ``False`` if every run completed with
    none failing, ``None`` if indeterminate — the check-runs couldn't be
    fetched (``check_runs is None``), some are still running, or cancelled or
    stale. No runs at all counts as passing: nothing is failing (e.g.
    ``[skip ci]``).
    """
    if check_runs is None:
        return None
    if any(run.conclusion in _FAILING_CI_CONCLUSIONS for run in check_runs):
        return True
    # No failures —> only report a definitive pass once every run has completed
    # with a verdict.
    if any(
        run.status != "completed" or run.conclusion in _INCONCLUSIVE_CI_CONCLUSIONS
        for run in check_runs
    ):
        return None
    return False


def remove_ignored_activity(
    pr_info: PRInfo,
    *,
    excluded_logins: set[str] | None = None,
    mute_self_review_comments: bool = False,
) -> PRInfo:
    """Drop the reviews and review requests that should not count for a scope.

    - ``excluded_logins``: lowercased set of logins to ignore entirely, both as
      reviewers and as requested reviewers.
    - ``mute_self_review_comments``: if True, COMMENTED reviews authored by the
      PR author are dropped (mirrors the per-scope ``mute_self_reviews`` flag).
    """
    reviews = pr_info.reviews
    requested = pr_info.requested_reviewers
    if mute_self_review_comments and pr_info.author_login:
        author = pr_info.author_login.lower()
        reviews = tuple(
            r
            for r in reviews
            if not (r.user_login.lower() == author and r.state == ReviewState.COMMENTED)
        )
    if excluded_logins:
        reviews = tuple(r for r in reviews if r.user_login.lower() not in excluded_logins)
        requested = tuple(login for login in requested if login.lower() not in excluded_logins)
    if reviews is pr_info.reviews and requested == pr_info.requested_reviewers:
        return pr_info
    return pr_info.model_copy(update={"reviews": reviews, "requested_reviewers": requested})


def resolve_ci_passed(pr_info: PRInfo) -> bool:
    """Whether an open PR's CI has definitively passed, so a CI-failure emoji is stale.

    Indeterminate CI (``None``) never counts. Nor does a merged or closed PR,
    where the last CI state is left as it was.
    """
    if pr_info.merged or pr_info.state != "open":
        return False
    return pr_info.ci_failing is False


def resolve_re_review_requested(pr_info: PRInfo) -> bool:
    """Whether an open PR is waiting on a reviewer who has already reviewed it.

    GitHub drops a reviewer from the requested list once they submit a review,
    so a requested reviewer with a prior review was re-requested.
    """
    if pr_info.merged or pr_info.state != "open":
        return False
    reviewed = {r.user_login.lower() for r in pr_info.reviews if r.state != ReviewState.PENDING}
    return any(login.lower() in reviewed for login in pr_info.requested_reviewers)


def _status_from_reviews(reviews: Sequence[Review]) -> PRStatus:
    """Resolve the status of an open PR from its reviews.

    Priority order:
    1. changes_requested (latest review from any reviewer) → CHANGES_REQUESTED
    2. approved (latest reviews, no outstanding changes_requested) → APPROVED
    3. commented (review exists but not approved/changes_requested) → COMMENTED
    4. open (no reviews yet) → OPEN
    """
    # Latest review per reviewer, skipping DISMISSED and PENDING.
    latest_reviews: dict[str, ReviewState] = {}
    for review in reviews:
        if review.state == ReviewState.PENDING:
            continue
        if review.state == ReviewState.DISMISSED:
            latest_reviews.pop(review.user_login, None)
            continue
        latest_reviews[review.user_login] = review.state

    review_states = set(latest_reviews.values())

    if ReviewState.CHANGES_REQUESTED in review_states:
        return PRStatus.CHANGES_REQUESTED

    if ReviewState.APPROVED in review_states:
        return PRStatus.APPROVED

    if ReviewState.COMMENTED in review_states:
        return PRStatus.COMMENTED

    return PRStatus.OPEN


def resolve_pr_status(pr_info: PRInfo) -> PRStatus:
    """Determine the PR status based on state and reviews.

    Priority order:
    1. merged → MERGED
    2. closed (not merged) → CLOSED
    3. otherwise the PR is open; see `_status_from_reviews`.
    """
    if pr_info.merged:
        return PRStatus.MERGED

    if pr_info.state == "closed":
        return PRStatus.CLOSED

    return _status_from_reviews(pr_info.reviews)
