from datetime import datetime

from pydantic import BaseModel

from prbot.domain.adr.value_objects import NotionPageId
from prbot.domain.tracking.value_objects import MessageRef


class TrackedADR(BaseModel, frozen=True):
    """An ADR review post the bot keeps in step with its ADR's consensus table.

    - ``applied_emojis``: the reactions the bot has put on the post, by name.
    - ``last_edited_time``: when the ADR page had last been edited as of the
      last sync, so a page that hasn't changed since can be skipped.
    """

    page_id: NotionPageId
    message_ref: MessageRef
    applied_emojis: frozenset[str] = frozenset()
    scope_keys: tuple[str, ...] = ()
    last_edited_time: datetime | None = None
