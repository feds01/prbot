from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from prbot.application.route_message import RouteMessage
from prbot.config import SlackConfig
from prbot.integration.slack.handler import SlackIntegration, is_trackable


def _integration() -> SlackIntegration:
    return SlackIntegration(
        config=SlackConfig(
            # Dummy credentials; no request ever leaves the process.
            bot_token="xoxb-test",  # noqa: S106
            signing_secret="secret",  # noqa: S106
        ),
        route_message=MagicMock(),
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


_ADR_POST = ":memo: ADR to review <https://app.notion.com/p/31ed89c32a998009b89ee7e5d7bcf672|ADR>"
_WITH_ADRS = RouteMessage(pr_messages=MagicMock(), adr_messages=MagicMock())


class TestIsTrackable:
    @pytest.mark.parametrize("text", ["please review https://github.com/o/r/pull/12", _ADR_POST])
    def test_pr_links_and_adr_reviews_are_routed(self, text: str) -> None:
        assert is_trackable(text, _WITH_ADRS)

    @pytest.mark.parametrize(
        "text",
        [
            "morning all",
            "<https://app.notion.com/p/31ed89c32a998009b89ee7e5d7bcf672|Notes>",
            ":memo: ADR to review, link to follow",
        ],
    )
    def test_other_messages_are_dropped(self, text: str) -> None:
        assert not is_trackable(text, _WITH_ADRS)

    def test_adr_reviews_are_dropped_without_an_adr_pipeline(self) -> None:
        assert not is_trackable(_ADR_POST, RouteMessage(pr_messages=MagicMock()))
