import pytest
from pydantic import ValidationError

from prbot.domain.tracking.value_objects import PRUrl


class TestPRUrl:
    def test_is_frozen(self) -> None:
        pr = PRUrl(owner="a", repo="b", number=1)
        with pytest.raises(ValidationError):
            pr.owner = "c"  # ty: ignore[invalid-assignment]
