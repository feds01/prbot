from unittest.mock import MagicMock

from fastapi import FastAPI

from prbot.config import SlackConfig
from prbot.integration.slack.handler import SlackIntegration


def _integration() -> SlackIntegration:
    return SlackIntegration(
        config=SlackConfig(
            # Dummy credentials; no request ever leaves the process.
            bot_token="xoxb-test",  # noqa: S106
            signing_secret="secret",  # noqa: S106
        ),
        handle_incoming_message=MagicMock(),
        cursor_repo=MagicMock(),
        backfill=MagicMock(),
        command_dispatcher=MagicMock(),
    )


class TestSlackIntegrationWiring:
    """Listener and route callbacks must have runtime-resolvable annotations.

    slack-bolt and FastAPI both resolve a callback's annotations when it is
    registered, so any name used in one of those signatures has to be importable
    at runtime — not only under `TYPE_CHECKING`.
    """

    def test_listeners_register(self) -> None:
        # Construction runs _setup_events and _setup_commands.
        assert _integration() is not None

    def test_routes_register(self) -> None:
        app = FastAPI()
        _integration().register_routes(app)
        assert "/slack/events" in {getattr(r, "path", "") for r in app.routes}
