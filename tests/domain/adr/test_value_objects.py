import pytest
from pydantic import ValidationError

from prbot.domain.adr.value_objects import NotionPageId


class TestNotionPageId:
    def test_accepts_the_undashed_form_used_in_links(self) -> None:
        page_id = NotionPageId(value="415d89c32a99811787e9f25e40da5970")
        assert str(page_id) == "415d89c32a99811787e9f25e40da5970"

    def test_normalises_the_dashed_form_used_by_the_api(self) -> None:
        dashed = NotionPageId(value="415D89C3-2A99-8117-87E9-F25E40DA5970")
        assert dashed == NotionPageId(value="415d89c32a99811787e9f25e40da5970")

    @pytest.mark.parametrize(
        "raw",
        ["", "415d89c32a99811787e9f25e40da597", "415d89c32a99811787e9f25e40da5970ff", "not-an-id"],
    )
    def test_rejects_anything_else(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            NotionPageId(value=raw)
