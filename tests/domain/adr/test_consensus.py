from datetime import UTC, datetime
from decimal import Decimal

import pytest

from prbot.domain.adr.consensus import (
    AdrEmoji,
    ConsensusVerdict,
    evaluate_consensus,
    fallback_for,
    read_vote,
    wanted_emojis,
)
from prbot.domain.adr.value_objects import AdrSnapshot, ConsensusRow, NotionPageId
from prbot.domain.emoji.value_objects import EmojiConfig

_PAGE = NotionPageId(value="415d89c32a9980088a3fd6f391acb650")
_EDITED = datetime(2026, 11, 12, 9, 41, tzinfo=UTC)


def _snapshot(version: int | None, *rows: tuple[str, int | None, str]) -> AdrSnapshot:
    return AdrSnapshot(
        page_id=_PAGE,
        version=version,
        last_edited_time=_EDITED,
        rows=tuple(ConsensusRow(reviewer=r, version=v, comment=c) for r, v, c in rows),
    )


APPROVED = "approved"
CHANGES_REQUESTED = "changes requested"
COMMENTED = "commented"
NOTHING: set[str] = set()


def _outcome(*comments: str) -> set[str]:
    """Evaluate one row per comment, by different reviewers, on version 1."""
    rows = [(f"reviewer-{i}", 1, comment) for i, comment in enumerate(comments)]
    verdict = evaluate_consensus(_snapshot(1, *rows))
    flags = {
        APPROVED: verdict.approved,
        CHANGES_REQUESTED: verdict.changes_requested,
        COMMENTED: verdict.commented,
    }
    return {name for name, on in flags.items() if on}


def _names(emojis: tuple[AdrEmoji, ...]) -> list[str]:
    return [e.emoji for e in emojis]


class TestReadVote:
    @pytest.mark.parametrize(
        ("cell", "score"),
        [
            ("+2", Decimal(2)),
            ("+1", Decimal(1)),
            ("+0", Decimal(0)),
            ("-0", Decimal(0)),
            ("-1", Decimal(-1)),
            ("-2", Decimal(-2)),
            ("+0.5", Decimal("0.5")),
            ("+1.5", Decimal("1.5")),
            ("  +1 ", Decimal(1)),
            ("\N{MINUS SIGN}1", Decimal(-1)),
            ("\N{EN DASH}2", Decimal(-2)),
            ("\N{EM DASH}1", Decimal(-1)),
        ],
    )
    def test_reads_the_leading_score(self, cell: str, score: Decimal) -> None:
        assert read_vote(cell).score == score

    @pytest.mark.parametrize("cell", ["", "LGTM", "I have concerns", "2", "great, +1 from me"])
    def test_no_leading_signed_number_means_no_score(self, cell: str) -> None:
        assert read_vote(cell).score is None

    @pytest.mark.parametrize("cell", ["- 2 small nits, otherwise fine", "+ 1 from me"])
    def test_a_sign_apart_from_the_number_is_not_a_score(self, cell: str) -> None:
        vote = read_vote(cell)
        assert vote.score is None
        assert vote.has_comment is True

    @pytest.mark.parametrize(
        "cell",
        [
            "+1",
            "+2  ",
            "+1 LGTM",
            "+1 lgtm!",
            "+1 LGTM 👍",
            "+2 ✅",
            "+1 Looks good to me.",
            "+1 ship it, looks great",
            "-0",
            "LGTM",
            "",
        ],
    )
    def test_score_and_sign_offs_alone_are_not_a_comment(self, cell: str) -> None:
        assert read_vote(cell).has_comment is False

    @pytest.mark.parametrize(
        "cell",
        [
            "+1 LGTM, but can we add a sequence diagram?",
            "+0.5 I think we should consider some parts here a bit more",
            "+1.5\nThat looks good, ListNotices will return a list of Notice structs",
            "-1 blocking on the retry semantics",
            "I'm not sure about the producer",
            "+1 nice idea",
        ],
    )
    def test_anything_else_after_the_score_is_a_comment(self, cell: str) -> None:
        assert read_vote(cell).has_comment is True


class TestEvaluateConsensus:
    def test_empty_table_has_only_a_version(self) -> None:
        verdict = evaluate_consensus(_snapshot(1))
        assert verdict == ConsensusVerdict(version=1)

    @pytest.mark.parametrize(
        ("score", "outcome"),
        [
            ("+2", {APPROVED}),
            ("+1", {APPROVED}),
            ("+0.5", NOTHING),
            ("+0", NOTHING),
            ("-0", NOTHING),
            ("-0.5", NOTHING),
            ("-1", {CHANGES_REQUESTED}),
            ("-2", {CHANGES_REQUESTED}),
        ],
    )
    def test_what_one_score_means(self, score: str, outcome: set[str]) -> None:
        assert _outcome(score) == outcome

    @pytest.mark.parametrize(
        ("comments", "outcome"),
        [
            (["+1 LGTM"], {APPROVED}),
            (["+0.5 consider X"], {COMMENTED}),
            (["+1", "+0.5 consider X"], {APPROVED, COMMENTED}),
            (["+2", "-1"], {APPROVED, CHANGES_REQUESTED}),
            (["+2", "-1 blocking on Y"], {APPROVED, CHANGES_REQUESTED, COMMENTED}),
        ],
    )
    def test_comments_and_several_reviewers(self, comments: list[str], outcome: set[str]) -> None:
        assert _outcome(*comments) == outcome

    def test_only_rows_for_the_current_version_count(self) -> None:
        verdict = evaluate_consensus(
            _snapshot(
                3,
                ("alex", 2, "+0.5 clear up who produces messages"),
                ("yves", 2, "-1"),
                ("alex", 3, "+1 LGTM"),
            )
        )
        assert verdict == ConsensusVerdict(version=3, approved=True)

    def test_a_version_bump_starts_from_nothing(self) -> None:
        verdict = evaluate_consensus(_snapshot(2, ("tom", 1, "+2"), ("yves", 1, "-1 blocking")))
        assert verdict == ConsensusVerdict(version=2)

    def test_rows_without_a_readable_version_are_ignored(self) -> None:
        # e.g. a date typed into the Version cell
        verdict = evaluate_consensus(_snapshot(1, ("bryan", None, "+1")))
        assert verdict == ConsensusVerdict(version=1)

    def test_a_reviewers_last_row_replaces_their_earlier_one(self) -> None:
        verdict = evaluate_consensus(
            _snapshot(2, ("yves", 2, "-1 needs a retry story"), ("yves", 2, "+1"))
        )
        assert verdict == ConsensusVerdict(version=2, approved=True)

    def test_rows_without_a_reviewer_each_count(self) -> None:
        verdict = evaluate_consensus(_snapshot(1, ("", 1, "+1"), ("", 1, "-1")))
        assert verdict == ConsensusVerdict(version=1, approved=True, changes_requested=True)

    def test_falls_back_to_the_highest_table_version_without_a_page_version(self) -> None:
        verdict = evaluate_consensus(_snapshot(None, ("tom", 1, "-1"), ("tom", 2, "+1")))
        assert verdict == ConsensusVerdict(version=2, approved=True)

    def test_no_version_anywhere_means_nothing_to_show(self) -> None:
        verdict = evaluate_consensus(_snapshot(None, ("tom", None, "+1")))
        assert verdict == ConsensusVerdict(version=None)


class TestWantedEmojis:
    config = EmojiConfig()

    def test_version_only(self) -> None:
        emojis = wanted_emojis(ConsensusVerdict(version=1), self.config)
        assert emojis == (AdrEmoji(emoji="one", fallback="1\N{VARIATION SELECTOR-16}⃣"),)

    @pytest.mark.parametrize(
        ("version", "name"), [(1, "one"), (2, "two"), (9, "nine"), (10, "keycap_ten")]
    )
    def test_version_names(self, version: int, name: str) -> None:
        assert _names(wanted_emojis(ConsensusVerdict(version=version), self.config)) == [name]

    @pytest.mark.parametrize("version", [None, 0, 11])
    def test_no_version_emoji_outside_one_to_ten(self, version: int | None) -> None:
        assert wanted_emojis(ConsensusVerdict(version=version), self.config) == ()

    def test_approved(self) -> None:
        emojis = wanted_emojis(ConsensusVerdict(version=2, approved=True), self.config)
        assert _names(emojis) == ["two", "git-approved"]
        assert emojis[1].fallback == "\N{WHITE HEAVY CHECK MARK}"

    def test_changes_requested(self) -> None:
        verdict = ConsensusVerdict(version=2, changes_requested=True)
        assert _names(wanted_emojis(verdict, self.config)) == ["two", "git-changes-requested"]

    def test_contested_replaces_approved_and_changes_requested(self) -> None:
        verdict = ConsensusVerdict(version=1, approved=True, changes_requested=True, commented=True)
        emojis = wanted_emojis(verdict, self.config)
        assert _names(emojis) == ["one", "boxing_glove", "speech_balloon"]
        assert emojis[1].fallback == "\N{BOXING GLOVE}"

    def test_commented(self) -> None:
        verdict = ConsensusVerdict(version=3, commented=True)
        assert _names(wanted_emojis(verdict, self.config)) == ["three", "speech_balloon"]

    def test_uses_the_scopes_configured_names(self) -> None:
        config = EmojiConfig(approved="shipit", adr_contested="crossed_swords")
        assert _names(wanted_emojis(ConsensusVerdict(version=1, approved=True), config)) == [
            "one",
            "shipit",
        ]
        contested = ConsensusVerdict(version=1, approved=True, changes_requested=True)
        assert _names(wanted_emojis(contested, config)) == ["one", "crossed_swords"]

    def test_a_name_shared_by_two_emoji_is_wanted_once(self) -> None:
        config = EmojiConfig(approved="thumbsup", commented="thumbsup")
        verdict = ConsensusVerdict(version=1, approved=True, commented=True)
        assert _names(wanted_emojis(verdict, config)) == ["one", "thumbsup"]


class TestFallbackFor:
    config = EmojiConfig(approved="shipit")

    @pytest.mark.parametrize(
        ("emoji", "fallback"),
        [
            ("three", "3\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}"),
            ("keycap_ten", "\N{KEYCAP TEN}"),
            ("shipit", "\N{WHITE HEAVY CHECK MARK}"),
            ("git-changes-requested", "\N{NO ENTRY SIGN}"),
            ("boxing_glove", "\N{BOXING GLOVE}"),
            ("speech_balloon", "\N{SPEECH BALLOON}"),
        ],
    )
    def test_matches_what_wanted_emojis_gives(self, emoji: str, fallback: str) -> None:
        assert fallback_for(emoji, self.config) == fallback

    def test_unknown_emoji_has_none(self) -> None:
        # The default approved name, no longer configured for this scope.
        assert fallback_for("git-approved", self.config) is None
