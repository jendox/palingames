from __future__ import annotations

import re

_SECRET_QUERY_TOKEN_RE = re.compile(r"(?i)(token=)([^&\s'\"]+)")


def mask_secret_query(text: str) -> str:
    if not text:
        return text
    return _SECRET_QUERY_TOKEN_RE.sub(r"\1***", text)
