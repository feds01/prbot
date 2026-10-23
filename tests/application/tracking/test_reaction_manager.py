import pytest

from prbot.application.tracking.reaction_manager import Reaction, ReactionManager
from prbot.domain.emoji.value_objects import EmojiConfig
from prbot.domain.tracking.entities import TrackedPR
from prbot.domain.tracking.value_objects import (
    MessageRef,
    PRInfo,
    PRStatus,
    PRUrl,
    Review,
    ReviewState,
)

APPROVED_EMOJI = EmojiConfig().approved
CI_EMOJI = EmojiConfig().ci_failed
RE_REVIEW_EMOJI = EmojiConfig().re_review_requested


def _tracked(applied: frozenset[str] = frozenset()) -> TrackedPR:
    return TrackedPR(
        pr_url=PRUrl(owner="o", repo="r", number=1),
        message_ref=MessageRef(integration_id="slack", ref="C1:1.2"),
        applied_emojis=applied,
    )


def _pr_info(ci_failing: bool | None) -> PRInfo:
    return PRInfo(state="open", merged=False, reviews=(), ci_failing=ci_failing)


class TestReactionManagerPlan:
    def test_plans_review_and_ci_emoji_together(self) -> None:
        plan = ReactionManager.plan(EmojiConfig(), PRStatus.APPROVED, _pr_info(True), _tracked())
        assert plan == [
            Reaction(APPROVED_EMOJI, EmojiConfig.fallback_for_status("approved")),
            Reaction(CI_EMOJI, EmojiConfig.fallback_for_status("ci_failed")),
        ]

    def test_plans_review_only_when_ci_not_failing(self) -> None:
        plan = ReactionManager.plan(EmojiConfig(), PRStatus.APPROVED, _pr_info(False), _tracked())
        assert plan == [Reaction(APPROVED_EMOJI, EmojiConfig.fallback_for_status("approved"))]

    def test_plans_nothing_for_status_without_emoji(self) -> None:
        plan = ReactionManager.plan(EmojiConfig(), PRStatus.OPEN, _pr_info(False), _tracked())
        assert plan == []

    def test_skips_emoji_already_on_message(self) -> None:
        tracked = _tracked(applied=frozenset({APPROVED_EMOJI}))
        plan = ReactionManager.plan(EmojiConfig(), PRStatus.APPROVED, _pr_info(True), tracked)
        assert plan == [Reaction(CI_EMOJI, EmojiConfig.fallback_for_status("ci_failed"))]

    def test_dedupes_when_review_and_ci_share_an_emoji(self) -> None:
        # A workspace could map the review status and CI failure to the same name;
        # the plan must still add it only once.
        config = EmojiConfig(approved="same", ci_failed="same")
        plan = ReactionManager.plan(config, PRStatus.APPROVED, _pr_info(True), _tracked())
        assert plan == [Reaction("same", EmojiConfig.fallback_for_status("approved"))]

    def test_plans_re_review_alongside_review_status(self) -> None:
        info = PRInfo(
            state="open",
            merged=False,
            reviews=(Review(user_login="bob", state=ReviewState.APPROVED),),
            requested_reviewers=("bob",),
        )
        plan = ReactionManager.plan(EmojiConfig(), PRStatus.APPROVED, info, _tracked())
        assert plan == [
            Reaction(APPROVED_EMOJI, EmojiConfig.fallback_for_status("approved")),
            Reaction(RE_REVIEW_EMOJI, EmojiConfig.fallback_for_status("re_review_requested")),
        ]

    def test_skips_re_review_already_on_message(self) -> None:
        info = PRInfo(
            state="open",
            merged=False,
            reviews=(Review(user_login="bob", state=ReviewState.COMMENTED),),
            requested_reviewers=("bob",),
        )
        tracked = _tracked(applied=frozenset({"speech_balloon", RE_REVIEW_EMOJI}))
        assert ReactionManager.plan(EmojiConfig(), PRStatus.COMMENTED, info, tracked) == []


def _re_review_info(requested: tuple[str, ...]) -> PRInfo:
    return PRInfo(
        state="open",
        merged=False,
        reviews=(Review(user_login="bob", state=ReviewState.APPROVED),),
        requested_reviewers=requested,
    )


RE_REVIEW_REACTION = Reaction(
    RE_REVIEW_EMOJI, EmojiConfig.fallback_for_status("re_review_requested")
)
CI_REACTION = Reaction(CI_EMOJI, EmojiConfig.fallback_for_status("ci_failed"))


class TestReactionManagerPlanRemovals:
    def test_removes_re_review_once_nobody_is_waiting(self) -> None:
        tracked = _tracked(applied=frozenset({RE_REVIEW_EMOJI}))
        plan = ReactionManager.plan_removals(EmojiConfig(), _re_review_info(()), tracked)
        assert plan == [RE_REVIEW_REACTION]

    def test_keeps_re_review_while_still_requested(self) -> None:
        tracked = _tracked(applied=frozenset({RE_REVIEW_EMOJI}))
        plan = ReactionManager.plan_removals(EmojiConfig(), _re_review_info(("bob",)), tracked)
        assert plan == []

    def test_keeps_re_review_while_another_reviewer_is_waiting(self) -> None:
        # bob re-reviewed and left the requested list; carol still hasn't.
        info = PRInfo(
            state="open",
            merged=False,
            reviews=(
                Review(user_login="bob", state=ReviewState.APPROVED),
                Review(user_login="carol", state=ReviewState.CHANGES_REQUESTED),
            ),
            requested_reviewers=("carol",),
        )
        tracked = _tracked(applied=frozenset({RE_REVIEW_EMOJI}))
        assert ReactionManager.plan_removals(EmojiConfig(), info, tracked) == []

    def test_removes_re_review_when_only_first_requests_remain(self) -> None:
        # dave was requested for the first time, so nobody is waiting to *re*-review.
        tracked = _tracked(applied=frozenset({RE_REVIEW_EMOJI}))
        plan = ReactionManager.plan_removals(EmojiConfig(), _re_review_info(("dave",)), tracked)
        assert plan == [RE_REVIEW_REACTION]

    def test_nothing_to_remove_when_not_applied(self) -> None:
        plan = ReactionManager.plan_removals(EmojiConfig(), _re_review_info(()), _tracked())
        assert plan == []

    def test_keeps_emoji_shared_with_another_status(self) -> None:
        # Removing it would also strip the approved reaction it doubles as.
        config = EmojiConfig(approved="same", re_review_requested="same")
        tracked = _tracked(applied=frozenset({"same"}))
        assert ReactionManager.plan_removals(config, _re_review_info(()), tracked) == []

    def test_removes_ci_emoji_once_ci_passes(self) -> None:
        tracked = _tracked(applied=frozenset({CI_EMOJI}))
        plan = ReactionManager.plan_removals(EmojiConfig(), _pr_info(False), tracked)
        assert plan == [CI_REACTION]

    @pytest.mark.parametrize("ci_failing", [True, None])
    def test_keeps_ci_emoji_unless_ci_passes(self, ci_failing: bool | None) -> None:
        tracked = _tracked(applied=frozenset({CI_EMOJI}))
        assert ReactionManager.plan_removals(EmojiConfig(), _pr_info(ci_failing), tracked) == []

    def test_keeps_ci_emoji_on_merged_pr(self) -> None:
        info = PRInfo(state="closed", merged=True, reviews=(), ci_failing=False)
        tracked = _tracked(applied=frozenset({CI_EMOJI}))
        assert ReactionManager.plan_removals(EmojiConfig(), info, tracked) == []

    def test_keeps_ci_emoji_shared_with_another_status(self) -> None:
        config = EmojiConfig(approved="same", ci_failed="same")
        tracked = _tracked(applied=frozenset({"same"}))
        assert ReactionManager.plan_removals(config, _pr_info(False), tracked) == []

    def test_removes_ci_and_re_review_emoji_together(self) -> None:
        info = PRInfo(state="open", merged=False, reviews=(), ci_failing=False)
        tracked = _tracked(applied=frozenset({CI_EMOJI, RE_REVIEW_EMOJI}))
        plan = ReactionManager.plan_removals(EmojiConfig(), info, tracked)
        assert plan == [CI_REACTION, RE_REVIEW_REACTION]


class FakeReactions:
    def __init__(self) -> None:
        self.added: list[tuple[MessageRef, str, str | None]] = []
        self.removed: list[tuple[MessageRef, str, str | None]] = []

    async def add_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        self.added.append((message_ref, emoji, fallback_emoji))

    async def remove_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        self.removed.append((message_ref, emoji, fallback_emoji))


class TestReactionManagerApply:
    async def test_applies_each_reaction_and_returns_emojis(self) -> None:
        fake = FakeReactions()
        manager = ReactionManager(fake)
        ref = MessageRef(integration_id="slack", ref="C1:1.2")
        reactions = [Reaction("a", "A"), Reaction("b", None)]

        added = await manager.apply(ref, reactions)

        assert added == ["a", "b"]
        assert fake.added == [(ref, "a", "A"), (ref, "b", None)]


class TestReactionManagerRemove:
    async def test_removes_each_reaction_and_returns_emojis(self) -> None:
        fake = FakeReactions()
        manager = ReactionManager(fake)
        ref = MessageRef(integration_id="slack", ref="C1:1.2")

        removed = await manager.remove(ref, [Reaction("a", "A")])

        assert removed == ["a"]
        assert fake.removed == [(ref, "a", "A")]
