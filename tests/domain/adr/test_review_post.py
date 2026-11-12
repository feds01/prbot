import pytest

from prbot.domain.adr.review_post import find_adr_review
from prbot.domain.adr.value_objects import NotionPageId

_ID = "31ed89c32a998009b89ee7e5d7bcf672"
_PAGE = NotionPageId(value=_ID)


class TestFindAdrReview:
    @pytest.mark.parametrize(
        "text",
        [
            f":memo: ADR to review: <https://app.notion.com/p/{_ID}|ADR-014 Queues>",
            f":memo: ADR to review <https://app.notion.com/p/userled-io/ADR-014-Queues-{_ID}|t>",
            f"\N{MEMO} ADR to review https://www.notion.so/userled-io/Queues-{_ID}",
            f":memo: *ADR to review*\n<https://app.notion.com/p/{_ID}?pvs=25|ADR-014>",
            f":memo: adr TO REVIEW <https://app.notion.com/p/{_ID.upper()}|ADR-014>",
            f":memo: ADR to review <https://www.notion.so/{_ID}/|ADR-014>",
            f"\n  @here :memo: ADR to review <https://app.notion.com/p/{_ID}|ADR-014>",
            f"*:memo:* *ADR to review* <https://app.notion.com/p/{_ID}|ADR-014>",
            f":memo: ADRs to review: <https://app.notion.com/p/{_ID}|ADR-014>",
        ],
    )
    def test_finds_the_linked_adr(self, text: str) -> None:
        assert find_adr_review(text) == _PAGE

    def test_ignores_the_block_named_in_the_fragment(self) -> None:
        block = "0123456789abcdef0123456789abcdef"
        text = f":memo: ADR to review <https://app.notion.com/p/{_ID}#{block}|ADR-014>"
        assert find_adr_review(text) == _PAGE

    def test_first_notion_link_is_the_adr(self) -> None:
        other = "0123456789abcdef0123456789abcdef"
        text = (
            f":memo: ADR to review <https://app.notion.com/p/{_ID}|ADR-014>, "
            f"background in <https://app.notion.com/p/{other}|RFC>"
        )
        assert find_adr_review(text) == _PAGE

    def test_skips_notion_links_without_a_page_id(self) -> None:
        text = f":memo: ADR to review (see https://www.notion.so/help) https://notion.so/{_ID}"
        assert find_adr_review(text) == _PAGE

    @pytest.mark.parametrize(
        "query",
        [f"v=0123456789abcdef0123456789abcdef&p={_ID}&pm=s", f"v=x&amp;p={_ID}&amp;pm=s"],
    )
    def test_side_peek_link_names_the_page_not_the_database(self, query: str) -> None:
        database = "fedcba9876543210fedcba9876543210"
        text = f":memo: ADR to review <https://www.notion.so/userled-io/{database}?{query}|ADR>"
        assert find_adr_review(text) == _PAGE

    def test_database_view_links_are_not_the_adr(self) -> None:
        database = "fedcba9876543210fedcba9876543210"
        text = (
            f":memo: ADR to review in <https://www.notion.so/acme/{database}?v=1a2b|ADR list>: "
            f"<https://app.notion.com/p/{_ID}|ADR-014>"
        )
        assert find_adr_review(text) == _PAGE

    def test_notion_links_before_the_marker_are_not_the_adr(self) -> None:
        notes = "0123456789abcdef0123456789abcdef"
        text = (
            f"<https://notion.so/{notes}|Notes> :memo: ADR to review "
            f"<https://app.notion.com/p/{_ID}|ADR-014>"
        )
        assert find_adr_review(text) == _PAGE

    @pytest.mark.parametrize(
        "text",
        [
            # A PR post linking an ADR review post: the linked post's text follows.
            (
                "Prototype for <https://acme.slack.com/archives/C1/p1700000000000100>: "
                f"https://github.com/o/r/pull/5\n:memo: ADR to review <https://notion.so/{_ID}|ADR>"
            ),
            f"Morning!\n:memo: ADR to review <https://app.notion.com/p/{_ID}|ADR-014>",
            f"<https://app.notion.com/p/{_ID}|ADR-014> is ready",
            f":memo: Meeting notes <https://app.notion.com/p/{_ID}|Notes>",
            ":memo: ADR to review, link to follow",
            f":memo: ADR to review https://example.com/{_ID}",
            ":memo: ADR to review https://github.com/o/r/pull/1",
        ],
    )
    def test_not_a_review_post(self, text: str) -> None:
        assert find_adr_review(text) is None
