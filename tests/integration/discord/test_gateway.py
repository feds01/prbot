from unittest.mock import AsyncMock, MagicMock

import discord

from prbot.domain.tracking.value_objects import MessageRef
from prbot.integration.discord.gateway import (
    INTEGRATION_ID,
    DiscordGateway,
    decode_ref,
    encode_ref,
    message_text,
)


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


_REPEAT = "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"


def _gateway_with_message(guild_emojis: list[MagicMock]) -> tuple[DiscordGateway, MagicMock]:
    message = MagicMock(spec=discord.Message)
    message.remove_reaction = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=message)
    client = MagicMock(spec=discord.Client)
    client.get_channel.return_value = channel
    client.emojis = guild_emojis
    return DiscordGateway(client), message


class TestRemoveReaction:
    async def test_skips_name_that_is_not_a_guild_emoji(self) -> None:
        # No custom `repeat` in the guild, so add_reaction landed the 🔁 fallback;
        # only that is removed — the bare name is never sent to Discord.
        gateway, message = _gateway_with_message([])

        await gateway.remove_reaction(encode_ref("1", "2"), "repeat", _REPEAT)

        message.remove_reaction.assert_awaited_once()
        assert message.remove_reaction.await_args.args[0] == _REPEAT

    async def test_clears_both_custom_emoji_and_fallback(self) -> None:
        custom = MagicMock(spec=discord.Emoji)
        custom.name = "repeat"
        gateway, message = _gateway_with_message([custom])

        await gateway.remove_reaction(encode_ref("1", "2"), "repeat", _REPEAT)

        removed = [call.args[0] for call in message.remove_reaction.await_args_list]
        assert removed == [custom, _REPEAT]
