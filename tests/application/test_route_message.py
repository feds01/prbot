from prbot.application.route_message import RouteMessage
from prbot.domain.tracking.value_objects import MessageRef

_REF = MessageRef(integration_id="slack", ref="C1:1.0")


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[MessageRef, str, list[str] | None]] = []

    async def execute(
        self,
        message_ref: MessageRef,
        text: str,
        scope_keys: list[str] | None = None,
    ) -> None:
        self.calls.append((message_ref, text, scope_keys))


class TestRouteMessage:
    async def test_pr_message_goes_to_the_pr_pipeline(self) -> None:
        prs = RecordingHandler()
        await RouteMessage(pr_messages=prs).execute(
            _REF, "see https://github.com/o/r/pull/1", ["slack"]
        )
        assert prs.calls == [(_REF, "see https://github.com/o/r/pull/1", ["slack"])]
