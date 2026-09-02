#!/usr/bin/env python3
"""The unit rung across backend, frontend, and factory trust-boundary tests.

The count is the point: every required suite must run, pass, and report a non-zero count.
The historical ratchet remains based on the last measured backend+frontend total until a
new full run is observed on the validation host; factory tests are mandatory immediately
without inventing a higher floor.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "app" / "backend"
FRONTEND = ROOT / "app" / "frontend"
FACTORY = ROOT / "tests" / "factory"

BACKEND_PATTERN = re.compile(r"(\d+) passed")
FRONTEND_PATTERN = re.compile(r"Tests\s+(\d+) passed")
FACTORY_PATTERN = re.compile(r"Ran\s+(\d+)\s+tests?")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(
    label: str,
    cwd: Path,
    argv: list[str],
    pattern: re.Pattern[str],
    *,
    env: dict[str, str] | None = None,
) -> int | None:
    """Return the real test count, or None if a required suite failed or ran nothing."""
    if not cwd.exists():
        print(f"UNIT_MISSING {label}: {cwd} does not exist", flush=True)
        return None
    try:
        p = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            env=env,
        )
    except FileNotFoundError:
        print(f"UNIT_MISSING {label}: {argv[0]} is not on PATH", flush=True)
        return None
    except subprocess.TimeoutExpired:
        print(f"UNIT_TIMEOUT {label} after 900s", flush=True)
        return None

    out = ANSI.sub("", (p.stdout or "") + (p.stderr or ""))
    if p.returncode != 0:
        print(f"--- {label} ---", flush=True)
        print(out.strip()[-3000:], flush=True)
        print(f"UNIT_FAILED half={label}", flush=True)
        return None

    m = pattern.search(out)
    count = int(m.group(1)) if m else 0
    if count == 0:
        print(f"UNIT_ERROR {label}: exited 0 but reported 0 tests. Either the suite ran "
              f"nothing, or the count pattern no longer matches its output.", flush=True)
        return None

    print(f"  ok  {label} tests={count}", flush=True)
    return count


def main() -> int:
    backend = run("backend", BACKEND,
                  ["uv", "run", "pytest", "tests", "-q"], BACKEND_PATTERN)
    frontend = run("frontend", FRONTEND,
                   ["bun", "run", "test"], FRONTEND_PATTERN)
    factory_env = os.environ.copy()
    scripts_path = str(ROOT / "scripts")
    existing_pythonpath = factory_env.get("PYTHONPATH", "")
    factory_env["PYTHONPATH"] = (
        scripts_path + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    )
    factory = run(
        "factory",
        ROOT,
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/factory",
            "-p",
            "test_*.py",
        ],
        FACTORY_PATTERN,
        env=factory_env,
    )

    if backend is None or frontend is None or factory is None:
        return 1

    total = backend + frontend + factory
    print(f"UNIT_PASSED tests={total} backend={backend} frontend={frontend} factory={factory}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
