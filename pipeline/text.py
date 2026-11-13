from __future__ import annotations

import re
import unicodedata


def normalize(text: str | None) -> str:
    """Normalize text for matching: ASCII, lowercase, single spaces."""

    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()
