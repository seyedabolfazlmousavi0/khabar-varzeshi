"""
Run test_automation.py without manual Enter presses.

test_automation.py is not modified — this wrapper feeds newlines to stdin
so every input() call (pause_step + final close) continues automatically.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tracked_step pauses + skipped-image pause + final close (+ margin for errors)
AUTO_ENTER_COUNT = 25


def main() -> int:
    script = Path(__file__).resolve().parent / "test_automation.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input="\n" * AUTO_ENTER_COUNT,
        text=True,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
