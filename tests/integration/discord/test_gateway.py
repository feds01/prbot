from unittest.mock import MagicMock

import discord

from prbot.domain.tracking.value_objects import MessageRef
from prbot.integration.discord.gateway import INTEGRATION_ID, decode_ref, encode_ref, message_text


class TestEncodeDecodeRef:
    def test_encode_ref(self) -> None:
        ref = encode_ref("123456", "789012")
        assert ref == MessageRef(integration_id=INTEGRATION_ID, ref="123456:789012")

    def test_decode_ref(self) -> None:
        ref = MessageRef(integration_id=INTEGRATION_ID, ref="123456:789012")
        channel_id, message_id = decode_ref(ref)
        assert channel_id == 123456
        assert message_id == 789012

    def test_roundtrip(self) -> None:
        original_channel = "111222333"
        original_message = "444555666"
        ref = encode_ref(original_channel, original_message)
        channel_id, message_id = decode_ref(ref)
        assert channel_id == int(original_channel)
        assert message_id == int(original_message)


_PR_URL = "https://github.com/acme/widgets/pull/42"


def _message(content: str, *snapshot_contents: str) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.content = content
    message.message_snapshots = [
        MagicMock(spec=discord.MessageSnapshot, content=c) for c in snapshot_contents
    ]
    return message


class TestMessageText:
    def test_plain_message(self) -> None:
        assert message_text(_message(_PR_URL)) == _PR_URL

    def test_forwarded_message(self) -> None:
        # A forward has empty content; the original lives in the snapshot.
        assert message_text(_message("", _PR_URL)) == _PR_URL

    def test_empty_message(self) -> None:
        assert message_text(_message("")) == ""
