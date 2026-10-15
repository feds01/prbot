from prbot.domain.tracking.status_resolver import (
    remove_ignored_activity,
    resolve_ci_failing,
    resolve_pr_status,
    resolve_re_review_requested,
)
from prbot.domain.tracking.value_objects import CheckRun, PRInfo, PRStatus, Review, ReviewState


def _make_pr_info(
    *,
    state: str = "open",
    merged: bool = False,
    reviews: tuple[Review, ...] = (),
) -> PRInfo:
    return PRInfo(state=state, merged=merged, reviews=reviews)


def _review(user: str, state: ReviewState) -> Review:
    return Review(user_login=user, state=state)


class TestResolveStatus:
    def test_merged(self) -> None:
        pr = _make_pr_info(state="closed", merged=True)
        assert resolve_pr_status(pr) == PRStatus.MERGED

    def test_merged_overrides_reviews(self) -> None:
        pr = _make_pr_info(
            state="closed",
            merged=True,
            reviews=(_review("alice", ReviewState.CHANGES_REQUESTED),),
        )
        assert resolve_pr_status(pr) == PRStatus.MERGED

    def test_closed_not_merged(self) -> None:
        pr = _make_pr_info(state="closed", merged=False)
        assert resolve_pr_status(pr) == PRStatus.CLOSED

    def test_open_no_reviews(self) -> None:
        pr = _make_pr_info()
        assert resolve_pr_status(pr) == PRStatus.OPEN

    def test_open_with_approval(self) -> None:
        pr = _make_pr_info(reviews=(_review("alice", ReviewState.APPROVED),))
        assert resolve_pr_status(pr) == PRStatus.APPROVED

    def test_open_with_changes_requested(self) -> None:
        pr = _make_pr_info(reviews=(_review("alice", ReviewState.CHANGES_REQUESTED),))
        assert resolve_pr_status(pr) == PRStatus.CHANGES_REQUESTED

    def test_changes_requested_takes_priority_over_approval(self) -> None:
        pr = _make_pr_info(
            reviews=(
                _review("alice", ReviewState.APPROVED),
                _review("bob", ReviewState.CHANGES_REQUESTED),
            )
        )
        assert resolve_pr_status(pr) == PRStatus.CHANGES_REQUESTED

    def test_same_reviewer_approves_after_requesting_changes(self) -> None:
        """Latest review per reviewer wins — alice first requests changes, then approves."""
        pr = _make_pr_info(
            reviews=(
                _review("alice", ReviewState.CHANGES_REQUESTED),
                _review("alice", ReviewState.APPROVED),
            )
        )
        assert resolve_pr_status(pr) == PRStatus.APPROVED

    def test_commented_only(self) -> None:
        pr = _make_pr_info(reviews=(_review("alice", ReviewState.COMMENTED),))
        assert resolve_pr_status(pr) == PRStatus.COMMENTED

    def test_dismissed_reviews_are_ignored(self) -> None:
        pr = _make_pr_info(reviews=(_review("alice", ReviewState.DISMISSED),))
        assert resolve_pr_status(pr) == PRStatus.OPEN

    def test_pending_reviews_are_ignored(self) -> None:
        pr = _make_pr_info(reviews=(_review("alice", ReviewState.PENDING),))
        assert resolve_pr_status(pr) == PRStatus.OPEN

    def test_approved_with_dismissed_changes_requested(self) -> None:
        """Dismissed reviews don't count — only the approved review matters."""
        pr = _make_pr_info(
            reviews=(
                _review("alice", ReviewState.CHANGES_REQUESTED),
                _review("alice", ReviewState.DISMISSED),
                _review("bob", ReviewState.APPROVED),
            )
        )
        assert resolve_pr_status(pr) == PRStatus.APPROVED

    def test_comment_does_not_override_approval(self) -> None:
        """A comment from one reviewer doesn't override another's approval."""
        pr = _make_pr_info(
            reviews=(
                _review("alice", ReviewState.APPROVED),
                _review("bob", ReviewState.COMMENTED),
            )
        )
        assert resolve_pr_status(pr) == PRStatus.APPROVED


def _run(status: str, conclusion: str | None) -> CheckRun:
    return CheckRun(status=status, conclusion=conclusion)


class TestResolveCiFailing:
    def test_no_runs_is_indeterminate(self) -> None:
        assert resolve_ci_failing(()) is None

    def test_any_failing_conclusion_is_failing(self) -> None:
        runs = (_run("completed", "success"), _run("completed", "failure"))
        assert resolve_ci_failing(runs) is True

    def test_all_passing_is_not_failing(self) -> None:
        runs = (_run("completed", "success"), _run("completed", "neutral"))
        assert resolve_ci_failing(runs) is False

    def test_still_running_is_indeterminate(self) -> None:
        # No failures yet, but not every run has completed — don't call it a pass.
        runs = (_run("completed", "success"), _run("in_progress", None))
        assert resolve_ci_failing(runs) is None

    def test_failure_wins_over_incomplete(self) -> None:
        # A definitive failure short-circuits even if other runs are pending.
        runs = (_run("in_progress", None), _run("completed", "timed_out"))
        assert resolve_ci_failing(runs) is True

    def test_action_required_counts_as_failing(self) -> None:
        assert resolve_ci_failing((_run("completed", "action_required"),)) is True


def _requested(
    requested: tuple[str, ...],
    reviews: tuple[Review, ...] = (),
    *,
    state: str = "open",
    merged: bool = False,
) -> PRInfo:
    return PRInfo(state=state, merged=merged, reviews=reviews, requested_reviewers=requested)


class TestResolveReReviewRequested:
    def test_no_requested_reviewers(self) -> None:
        info = _requested((), (_review("alice", ReviewState.APPROVED),))
        assert resolve_re_review_requested(info) is False

    def test_first_request_is_not_a_re_request(self) -> None:
        assert resolve_re_review_requested(_requested(("alice",))) is False

    def test_requested_reviewer_who_already_reviewed(self) -> None:
        info = _requested(("bob",), (_review("bob", ReviewState.CHANGES_REQUESTED),))
        assert resolve_re_review_requested(info) is True

    def test_re_request_after_comment_counts(self) -> None:
        info = _requested(("bob",), (_review("bob", ReviewState.COMMENTED),))
        assert resolve_re_review_requested(info) is True

    def test_pending_review_does_not_count(self) -> None:
        # A PENDING review is an unsubmitted draft, so it isn't a prior review.
        info = _requested(("bob",), (_review("bob", ReviewState.PENDING),))
        assert resolve_re_review_requested(info) is False

    def test_login_match_is_case_insensitive(self) -> None:
        info = _requested(("Bob",), (_review("bob", ReviewState.APPROVED),))
        assert resolve_re_review_requested(info) is True

    def test_other_reviewers_review_does_not_count(self) -> None:
        info = _requested(("bob",), (_review("alice", ReviewState.APPROVED),))
        assert resolve_re_review_requested(info) is False

    def test_several_re_requested_reviewers(self) -> None:
        reviews = (
            _review("bob", ReviewState.CHANGES_REQUESTED),
            _review("carol", ReviewState.APPROVED),
        )
        assert resolve_re_review_requested(_requested(("bob", "carol"), reviews)) is True

    def test_re_request_alongside_first_request(self) -> None:
        # dave is a first-time request, but bob's re-request is enough.
        reviews = (_review("bob", ReviewState.APPROVED),)
        assert resolve_re_review_requested(_requested(("bob", "dave"), reviews)) is True

    def test_only_first_requests_left(self) -> None:
        # bob re-reviewed and left the requested list; dave and erin never reviewed.
        reviews = (_review("bob", ReviewState.APPROVED),)
        assert resolve_re_review_requested(_requested(("dave", "erin"), reviews)) is False

    def test_merged_or_closed_pr_is_never_waiting(self) -> None:
        reviews = (_review("bob", ReviewState.APPROVED),)
        assert resolve_re_review_requested(_requested(("bob",), reviews, merged=True)) is False
        assert resolve_re_review_requested(_requested(("bob",), reviews, state="closed")) is False


class TestRemoveIgnoredActivity:
    def test_excluded_login_dropped_from_requested_reviewers(self) -> None:
        info = _requested(("Cursor", "bob"), (_review("Cursor", ReviewState.COMMENTED),))
        without_ignored = remove_ignored_activity(info, excluded_logins={"cursor"})
        assert without_ignored.requested_reviewers == ("bob",)
        assert resolve_re_review_requested(without_ignored) is False

    def test_unchanged_when_nothing_excluded(self) -> None:
        info = _requested(("bob",))
        assert remove_ignored_activity(info, excluded_logins={"cursor"}) is info
