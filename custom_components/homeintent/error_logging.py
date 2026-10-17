"""Privacy-safe, rate-limited logging for secondary failure boundaries."""

from __future__ import annotations

import logging
import time


_LAST_WARNING: dict[tuple[str, str, str], float] = {}


def log_rate_limited_boundary_failure(
    logger: logging.Logger,
    event: str,
    err: BaseException,
    *,
    interval: float = 60.0,
) -> bool:
    """Log one redacted warning per boundary/error kind and interval.

    Provider messages are intentionally excluded: rollback and setup errors
    may embed YAML paths, service payloads or credentials.  The boolean is a
    small test seam indicating whether this call emitted the warning.
    """
    error_kind = type(err).__name__
    key = (logger.name, event, error_kind)
    now = time.monotonic()
    last = _LAST_WARNING.get(key)
    if last is not None and now - last < interval:
        logger.debug("%s failed again (error_type=%s)", event, error_kind)
        return False
    _LAST_WARNING[key] = now
    logger.warning("%s failed (error_type=%s)", event, error_kind)
    return True
