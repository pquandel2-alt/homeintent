"""Fail when the test-bed log shows HomeIntent tracebacks or HA thread/blocking warnings.

    python check_log.py [config/home-assistant.run.log]

Acceptance rule of the live test: no traceback from HomeIntent, no
"from a thread other than the event loop" and no "Detected blocking call"
that points at HomeIntent code; no recorder read outside the recorder's
executor (7.9.3). Since 7.9.6 also, wherever they come from: a RuntimeWarning
(e.g. "coroutine ... was never awaited"), "Task was destroyed but it is
pending" and "Something is blocking Home Assistant".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_BAD_RE = re.compile(
    r"Traceback|from a thread other than the event loop|Detected blocking call|"
    r"Detected that custom integration 'homeintent'",
)

# 7.9.3: Home Assistant names no integration for this warning (the stack ends
# in the executor), so it counts without the "homeintent" filter; the test
# bed has no other code reading the recorder database.
_ANY_RE = re.compile(
    r"accesses the database without the database executor|"
    r"RuntimeWarning|was never awaited|Task was destroyed but it is pending|"
    r"Something is blocking Home Assistant"
)


def _entries(text: str) -> list[str]:
    entries: list[str] = []
    for line in text.splitlines():
        if _ENTRY_RE.match(line) or not entries:
            entries.append(line)
        else:
            entries[-1] += "\n" + line
    return entries


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "config" / "home-assistant.run.log"
    findings = [
        entry for entry in _entries(path.read_text(encoding="utf-8", errors="replace"))
        if (_BAD_RE.search(entry) and "homeintent" in entry.casefold()) or _ANY_RE.search(entry)
    ]
    for entry in findings:
        print(entry, end="\n\n")
    print(f"{len(findings)} HomeIntent-Befunde im Log {path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
