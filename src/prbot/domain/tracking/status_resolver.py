from collections.abc import Sequence

from prbot.domain.tracking.value_objects import CheckRun, PRInfo, PRStatus, Review, ReviewState

# check-run conclusions that count as a CI failure.
_FAILING_CI_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})


def resolve_ci_failing(check_runs: tuple[CheckRun, ...]) -> bool | None:
    """Derive the aggregate CI state for a commit from its check-runs.

    Tri-state: ``True`` if any run failed, ``False`` if every run completed with
    none failing, ``None`` if indeterminate — no runs, or some still running.
    """
    if not check_runs:
        return None
    if any(run.conclusion in _FAILING_CI_CONCLUSIONS for run in check_runs):
        return True
    # No failures —> only report a definitive pass once every run has completed.
    if any(run.status != "completed" for run in check_runs):
        return None
    return False


def filter_pr_info(
    pr_info: PRInfo,
    *,
    excluded_logins: set[str] | None = None,
    mute_self_review_comments: bool = False,
) -> PRInfo:
    """Drop reviews that should not influence status resolution for a scope.

    - ``excluded_logins``: lowercased set of logins to ignore entirely.
    - ``mute_self_review_comments``: if True, COMMENTED reviews authored by the
      PR author are dropped (mirrors the per-scope ``mute_self_reviews`` flag).
    """
    reviews = pr_info.reviews
    if mute_self_review_comments and pr_info.author_login:
        author = pr_info.author_login.lower()
        reviews = tuple(
            r
            for r in reviews
            if not (r.user_login.lower() == author and r.state == ReviewState.COMMENTED)
        )
    if excluded_logins:
        reviews = tuple(r for r in reviews if r.user_login.lower() not in excluded_logins)
    if reviews is pr_info.reviews:
        return pr_info
    return pr_info.model_copy(update={"reviews": reviews})


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
