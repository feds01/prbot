from prbot.application.route_message import RouteMessage
from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.tracking.value_objects import MessageRef

_REF = MessageRef(integration_id="slack", ref="C1:1.0")
_SCOPES = ["slack"]
_ID = "31ed89c32a998009b89ee7e5d7bcf672"
_PR_POST = "see https://github.com/o/r/pull/1"
_ADR_POST = f":memo: ADR to review <https://app.notion.com/p/{_ID}|ADR-014>"


class RecordingPRHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[MessageRef, str, list[str] | None]] = []

    async def execute(
        self, message_ref: MessageRef, text: str, scope_keys: list[str] | None = None
    ) -> None:
        self.calls.append((message_ref, text, scope_keys))


class RecordingAdrHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[MessageRef, NotionPageId, list[str] | None]] = []

    async def execute(
        self, message_ref: MessageRef, page_id: NotionPageId, scope_keys: list[str] | None = None
    ) -> None:
        self.calls.append((message_ref, page_id, scope_keys))


class TestRouteMessage:
    async def test_pr_message_goes_to_the_pr_pipeline(self) -> None:
        prs, adrs = RecordingPRHandler(), RecordingAdrHandler()
        await RouteMessage(pr_messages=prs, adr_messages=adrs).execute(_REF, _PR_POST, _SCOPES)

        assert prs.calls == [(_REF, _PR_POST, _SCOPES)]
        assert adrs.calls == []

    async def test_adr_post_goes_to_the_adr_pipeline(self) -> None:
        prs, adrs = RecordingPRHandler(), RecordingAdrHandler()
        await RouteMessage(pr_messages=prs, adr_messages=adrs).execute(_REF, _ADR_POST, _SCOPES)

        assert adrs.calls == [(_REF, NotionPageId(value=_ID), _SCOPES)]
        assert prs.calls == []

    async def test_adr_post_linking_a_pr_goes_to_the_adr_pipeline_only(self) -> None:
        prs, adrs = RecordingPRHandler(), RecordingAdrHandler()
        text = f"{_ADR_POST}\nPrototype: {_PR_POST}"
        await RouteMessage(pr_messages=prs, adr_messages=adrs).execute(_REF, text, _SCOPES)

        assert len(adrs.calls) == 1
        assert prs.calls == []

    async def test_without_an_adr_pipeline_adr_posts_go_to_the_pr_pipeline(self) -> None:
        prs = RecordingPRHandler()
        await RouteMessage(pr_messages=prs).execute(_REF, _ADR_POST, _SCOPES)

        assert prs.calls == [(_REF, _ADR_POST, _SCOPES)]

    def test_is_adr_review_only_with_an_adr_pipeline(self) -> None:
        with_adrs = RouteMessage(
            pr_messages=RecordingPRHandler(), adr_messages=RecordingAdrHandler()
        )
        assert with_adrs.is_adr_review(_ADR_POST)
        assert not with_adrs.is_adr_review(_PR_POST)
        assert not RouteMessage(pr_messages=RecordingPRHandler()).is_adr_review(_ADR_POST)
