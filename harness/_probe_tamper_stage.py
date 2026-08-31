#!/usr/bin/env python3
"""THROWAWAY adversarial probe. Never merged; exists to answer one empirical question.

Each genesis stage runs candidate-controlled code as a child of genesis_validate.py, on the same
runner and as the same user. The driver writes stage-<name>.json only after that child exits, and
the workflow's upload step runs after that. So: can a candidate stage spawn a detached process,
exit zero, wait for the driver's structured result to appear, rewrite a measurement, and have the
forged file be what gets uploaded?

This stage does exactly that, with a sentinel of 999999. The honest measurement it prints is 1.
Whatever reaches the aggregate answers the question.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

TARGET = os.environ.get("PROBE_TARGET", "stage-tamper-probe.json")
WITNESS = os.environ.get("PROBE_WITNESS", "probe-witness.txt")

CHILD = f'''
import json, time
from pathlib import Path

target = Path({TARGET!r})
witness = Path({WITNESS!r})
deadline = time.time() + 300
witness.write_text("child-started\\n", encoding="utf-8")
while time.time() < deadline:
    if target.is_file():
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.05)
            continue
        measurements = value.get("stage", {{}}).get("measurements") or {{}}
        if measurements:
            for key in list(measurements):
                measurements[key] = 999999
            value["stage"]["measurements"] = measurements
            target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
            witness.write_text("child-rewrote-target\\n", encoding="utf-8")
            break
    time.sleep(0.05)
else:
    witness.write_text("child-timed-out\\n", encoding="utf-8")
'''


def main() -> int:
    subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=os.getcwd(),
    )
    # The stage itself behaves perfectly: honest measurement, clean exit.
    print("PROBE_OK n=1", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
