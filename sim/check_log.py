"""Fail when the test-bed log shows HomeIntent tracebacks or HA thread/blocking warnings.

    python check_log.py [config/home-assistant.run.log]

Acceptance rule of the live test: no traceback from HomeIntent, no
"from a thread other than the event loop" and no "Detected blocking call"
that points at HomeIntent code.
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
        if _BAD_RE.search(entry) and "homeintent" in entry.casefold()
    ]
    for entry in findings:
        print(entry, end="\n\n")
    print(f"{len(findings)} HomeIntent-Befunde im Log {path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
