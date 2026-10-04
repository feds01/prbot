from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from prbot.domain.tracking.value_objects import MessageRef
from prbot.integration.slack.gateway import SlackGateway, encode_ref, message_text


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def gateway(mock_client: AsyncMock) -> SlackGateway:
    return SlackGateway(client=mock_client)


def _msg_ref() -> MessageRef:
    return encode_ref("C123", "1234.5678")


class TestSlackGateway:
    async def test_add_reaction_calls_api(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        await gateway.add_reaction(_msg_ref(), "eyes")

        mock_client.reactions_add.assert_awaited_once_with(
            channel="C123", timestamp="1234.5678", name="eyes"
        )

    async def test_add_reaction_ignores_already_reacted(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("already_reacted")

        # Should not raise
        await gateway.add_reaction(_msg_ref(), "eyes")

    async def test_add_reaction_raises_other_errors(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("channel_not_found")

        with pytest.raises(Exception, match="channel_not_found"):
            await gateway.add_reaction(_msg_ref(), "eyes")

    async def test_add_reaction_ignores_deleted_message(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("message_not_found")

        # Should not raise — a deleted message is a dead end, not an error.
        await gateway.add_reaction(_msg_ref(), "git-approved")

    async def test_add_reaction_skips_fallback_when_message_deleted(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.reactions_add.side_effect = Exception("message_not_found")

        await gateway.add_reaction(_msg_ref(), "git-approved", "\N{WHITE HEAVY CHECK MARK}")

        # No point retrying the fallback against a message that no longer exists.
        assert mock_client.reactions_add.await_count == 1

    async def test_cross_mark_resolves_to_slack_x_alias(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        # ❌ derives to "cross_mark", but Slack's alias is ":x:" — the resolver
        # must translate so the unicode fallback actually resolves on Slack.
        await gateway.add_reaction(_msg_ref(), "\N{CROSS MARK}")

        mock_client.reactions_add.assert_awaited_once_with(
            channel="C123", timestamp="1234.5678", name="x"
        )

    async def test_custom_primary_with_x_fallback(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        # Mirrors a workspace with a custom :ci_failed: emoji as primary and the
        # ❌ unicode fallback: primary works, so ❌ is never reached.
        await gateway.add_reaction(_msg_ref(), "ci_failed", "\N{CROSS MARK}")

        mock_client.reactions_add.assert_awaited_once_with(
            channel="C123", timestamp="1234.5678", name="ci_failed"
        )


_PR_URL = "https://github.com/acme/widgets/pull/42"


def _forward(text: str, *, is_share: bool = True) -> dict[str, object]:
    """Build a shared-message attachment, as Slack attaches it to a forwarded message."""
    return {"is_share": is_share, "is_msg_unfurl": True, "text": text}


class TestMessageText:
    def test_plain_message(self) -> None:
        assert message_text({"text": f"<{_PR_URL}>"}) == f"<{_PR_URL}>"

    def test_forward_without_comment(self) -> None:
        msg = {"text": "", "attachments": [_forward(f"<{_PR_URL}>")]}
        assert message_text(msg) == f"<{_PR_URL}>"

    def test_forward_with_comment(self) -> None:
        msg = {"text": "can someone look?", "attachments": [_forward(f"<{_PR_URL}>")]}
        assert message_text(msg) == f"can someone look?\n<{_PR_URL}>"

    def test_ignores_non_shared_attachments(self) -> None:
        # Link unfurls and app attachments aren't forwards. A linked Slack message
        # is read by fetching it (see TestResolveText), not from its unfurl.
        msg = {"text": "see docs", "attachments": [_forward(f"<{_PR_URL}>", is_share=False)]}
        assert message_text(msg) == "see docs"

    def test_tolerates_malformed_attachments(self) -> None:
        assert message_text({"text": "hi", "attachments": "nope"}) == "hi"
        assert message_text({"text": "hi", "attachments": ["nope"]}) == "hi"

    def test_empty_message(self) -> None:
        assert message_text({}) == ""


_LINKED_TS = "1700000000.123456"
_PERMALINK = "https://acme.slack.com/archives/C456/p1700000000123456"


class TestResolveText:
    async def test_follows_link_to_another_message(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_replies.return_value = {
            "messages": [{"ts": _LINKED_TS, "text": f"<{_PR_URL}>"}]
        }

        text = await gateway.resolve_text({"text": f":pr-bump: <{_PERMALINK}>"})

        assert text == f":pr-bump: <{_PERMALINK}>\n<{_PR_URL}>"
        mock_client.conversations_replies.assert_awaited_once_with(
            channel="C456", ts=_LINKED_TS, oldest=_LINKED_TS, latest=_LINKED_TS, inclusive=True
        )

    async def test_reads_each_linked_message_once(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_replies.return_value = {"messages": []}

        await gateway.resolve_text({"text": f"<{_PERMALINK}> <{_PERMALINK}|again>"})

        mock_client.conversations_replies.assert_awaited_once()

    async def test_picks_linked_reply_out_of_its_thread(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1690000000.000001", "text": "thread parent"},
                {"ts": _LINKED_TS, "text": f"<{_PR_URL}>"},
            ]
        }

        text = await gateway.resolve_text({"text": f"<{_PERMALINK}>"})

        assert text == f"<{_PERMALINK}>\n<{_PR_URL}>"

    async def test_reads_forward_in_linked_message(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_replies.return_value = {
            "messages": [{"ts": _LINKED_TS, "text": "", "attachments": [_forward(f"<{_PR_URL}>")]}]
        }

        text = await gateway.resolve_text({"text": f"<{_PERMALINK}>"})

        assert text == f"<{_PERMALINK}>\n<{_PR_URL}>"

    async def test_follows_links_one_level_only(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        other = "https://acme.slack.com/archives/C789/p1600000000000001"
        mock_client.conversations_replies.return_value = {
            "messages": [{"ts": _LINKED_TS, "text": f"<{other}>"}]
        }

        text = await gateway.resolve_text({"text": f"<{_PERMALINK}>"})

        assert text == f"<{_PERMALINK}>\n<{other}>"
        mock_client.conversations_replies.assert_awaited_once()

    async def test_skips_unreadable_link(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_replies.side_effect = SlackApiError(
            "not_in_channel", {"ok": False, "error": "not_in_channel"}
        )

        assert await gateway.resolve_text({"text": f"<{_PERMALINK}>"}) == f"<{_PERMALINK}>"

    async def test_no_links_no_lookups(self, gateway: SlackGateway, mock_client: AsyncMock) -> None:
        assert await gateway.resolve_text({"text": f"<{_PR_URL}>"}) == f"<{_PR_URL}>"
        mock_client.conversations_replies.assert_not_awaited()


class TestFetchChannelHistory:
    async def test_yields_forwarded_message_with_empty_text(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_history.return_value = {
            "messages": [
                {"ts": "1.0", "text": "", "attachments": [_forward(f"<{_PR_URL}>")]},
                {"ts": "2.0", "text": ""},
            ],
            "has_more": False,
        }

        items = [item async for item in gateway.fetch_channel_history("C123", "T1")]

        assert [(item.ts, item.text) for item in items] == [("1.0", f"<{_PR_URL}>")]

    async def test_yields_message_linking_to_a_pr_message(
        self, gateway: SlackGateway, mock_client: AsyncMock
    ) -> None:
        mock_client.conversations_history.return_value = {
            "messages": [{"ts": "1.0", "text": f"<{_PERMALINK}>"}],
            "has_more": False,
        }
        mock_client.conversations_replies.return_value = {
            "messages": [{"ts": _LINKED_TS, "text": f"<{_PR_URL}>"}]
        }

        items = [item async for item in gateway.fetch_channel_history("C123", "T1")]

        assert [item.text for item in items] == [f"<{_PERMALINK}>\n<{_PR_URL}>"]
