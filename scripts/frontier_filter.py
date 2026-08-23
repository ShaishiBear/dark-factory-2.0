#!/usr/bin/env python3
from __future__ import annotations
import json, re, subprocess, sys

BLOCK = re.compile(r"(?im)^Blocked by:\s*#([0-9]+)\s*$")

def issue_state(number: str) -> str:
    try:
        return subprocess.check_output(
            ["gh", "issue", "view", number, "--json", "state", "-q", ".state"],
            text=True, stderr=subprocess.DEVNULL, timeout=20,
        ).strip().upper()
    except Exception:
        return "UNKNOWN"

def main() -> None:
    candidates = json.load(sys.stdin)
    if not isinstance(candidates, list):
        raise SystemExit("FRONTIER_FAIL: expected candidate array")
    ready = []
    for issue in candidates:
        blockers = BLOCK.findall(issue.get("body") or "")
        open_blockers = [b for b in blockers if issue_state(b) != "CLOSED"]
        if open_blockers:
            print(f"FRONTIER_BLOCKED issue=#{issue.get('number')} blockers={','.join('#'+b for b in open_blockers)}", file=sys.stderr)
            continue
        ready.append(issue)
    json.dump(ready, sys.stdout, separators=(",", ":")); sys.stdout.write("\n")

if __name__ == "__main__": main()
