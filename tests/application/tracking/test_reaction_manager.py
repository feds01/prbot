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


class FakeReactions:
    def __init__(self) -> None:
        self.added: list[tuple[MessageRef, str, str | None]] = []

    async def add_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        self.added.append((message_ref, emoji, fallback_emoji))


class TestReactionManagerApply:
    async def test_applies_each_reaction_and_returns_emojis(self) -> None:
        fake = FakeReactions()
        manager = ReactionManager(fake)
        ref = MessageRef(integration_id="slack", ref="C1:1.2")
        reactions = [Reaction("a", "A"), Reaction("b", None)]

        added = await manager.apply(ref, reactions)

        assert added == ["a", "b"]
        assert fake.added == [(ref, "a", "A"), (ref, "b", None)]
