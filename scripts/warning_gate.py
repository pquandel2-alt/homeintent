#!/usr/bin/env python3
"""Run a script and fail on any RuntimeWarning, also one raised at GC (7.9.6).

``python -W error::RuntimeWarning script.py`` is not enough: a coroutine
that is never awaited is reported when it is garbage collected, inside
``__del__``, where Python can only print "Exception ignored in ..." and the
process still exits 0 (the calendar warning stayed invisible in green CI up
to 7.9.5). This gate runs the script in-process and additionally

- records every unraisable exception (``sys.unraisablehook``),
- records every ``RuntimeWarning`` (``warnings.showwarning``),
- records asyncio's "Task was destroyed but it is pending!" log record,
- forces a garbage collection after the script,

and exits 1 if anything was recorded - nothing is filtered or ignored.

    python -W error::RuntimeWarning scripts/warning_gate.py \\
        scripts/arbiter_shadow.py --check
"""

from __future__ import annotations

import gc
import logging
import runpy
import sys
import warnings
from typing import Any

_PATTERNS = ("was never awaited", "Task was destroyed but it is pending")


class _Recorder(logging.Handler):
    def __init__(self, found: list[str]) -> None:
        super().__init__(logging.ERROR)
        self._found = found

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if any(pattern in message for pattern in _PATTERNS):
            self._found.append(f"log: {message}")


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: warning_gate.py SCRIPT [ARGS...]", file=sys.stderr)
        return 2
    found: list[str] = []

    def _unraisable(unraisable: Any) -> None:
        found.append(
            f"unraisable: {type(unraisable.exc_value).__name__}: {unraisable.exc_value}"
            f" ({unraisable.object!r})"
        )

    original_show = warnings.showwarning

    def _show(message: Any, category: Any, filename: str, lineno: int, *args: Any) -> None:
        if issubclass(category, RuntimeWarning):
            found.append(f"warning: {filename}:{lineno}: {message}")
        original_show(message, category, filename, lineno, *args)

    sys.unraisablehook = _unraisable
    warnings.showwarning = _show
    warnings.simplefilter("always", RuntimeWarning)
    logging.getLogger().addHandler(_Recorder(found))
    sys.argv = list(argv)
    status: int = 0
    try:
        runpy.run_path(argv[0], run_name="__main__")
    except SystemExit as exit_:
        code = exit_.code
        status = code if isinstance(code, int) else (0 if code is None else 1)
    except RuntimeWarning as warning:
        found.append(f"raised: {warning}")
    for _ in range(3):
        gc.collect()
    for item in found:
        print(f"RuntimeWarning gate: {item}", file=sys.stderr)
    if found:
        print(f"RuntimeWarning gate: {len(found)} finding(s)", file=sys.stderr)
        return 1
    print("RuntimeWarning gate: 0 findings")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
