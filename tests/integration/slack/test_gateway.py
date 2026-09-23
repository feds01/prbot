from unittest.mock import AsyncMock

import pytest

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
        # Link unfurls and app attachments aren't forwards; a PR URL inside one
        # was never posted by a person.
        msg = {"text": "see docs", "attachments": [_forward(f"<{_PR_URL}>", is_share=False)]}
        assert message_text(msg) == "see docs"

    def test_tolerates_malformed_attachments(self) -> None:
        assert message_text({"text": "hi", "attachments": "nope"}) == "hi"
        assert message_text({"text": "hi", "attachments": ["nope"]}) == "hi"

    def test_empty_message(self) -> None:
        assert message_text({}) == ""


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
