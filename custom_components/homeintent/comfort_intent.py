"""Ground a vague comfort request in confirmed, structured preferences."""

from __future__ import annotations

import re

from .nlu.language_frontend import LanguageDocument


_COMFORT_RE = re.compile(
    r"\b(?:mach|mache|macht)\b.*\b(?:gemuetlich|gemütlich)(?:er)?\b"
    r"|\b(?:mach|mache|macht)\b.*\bkomfortabler\b"
)


def is_comfort_request(document: LanguageDocument) -> bool:
    return bool(_COMFORT_RE.search(document.normalized_text.casefold()))


