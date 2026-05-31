from __future__ import annotations

import re

_COLLAPSE = re.compile(r"\s+")


def normalize_review_text(text: str) -> str:
    return _COLLAPSE.sub(" ", text.strip())
