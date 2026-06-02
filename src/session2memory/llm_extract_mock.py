from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from session2memory.llm_extract import LlmExtractItem, parse_llm_extract_payload
from session2memory.models import SessionMessage


class MockLlmExtractBackend:
    def __init__(self, *, items_payload: list[dict[str, Any]]) -> None:
        self._items_payload = items_payload

    def extract(
        self,
        *,
        messages: Sequence[SessionMessage],
        workspace_id: str,
    ) -> list[LlmExtractItem]:
        del messages, workspace_id
        return parse_llm_extract_payload(json.dumps(self._items_payload))
