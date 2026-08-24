#!/usr/bin/env bash
# Dark Factory emergency stop check.
#
#   bash scripts/factory-stop.sh          exit 0 = clear to dispatch, exit 1 = STOPPED
#
# The repo-owned Python kernel calls this before every dispatch and again immediately before
# merge. Two mechanisms remain on purpose because they fail in different places:
#
#   1. A LOCAL kill file, useful even when the network is down.
#   2. A REMOTE open issue carrying `factory:stop`, reachable from GitHub/mobile.
#
# The remote half fails closed: if GitHub stop state cannot be read, the factory stops.

set -uo pipefail

REPO="${FACTORY_REPO:-ShaishiBear/dark-factory-2.0}"
KILL_FILE="${FACTORY_KILL_FILE:-${FACTORY_WORKDIR:-.}/.factory-stop}"
STOP_LABEL="factory:stop"

if [ -f "$KILL_FILE" ]; then
  echo "STOPPED: $KILL_FILE present. Remove it to resume."
  [ -s "$KILL_FILE" ] && echo "reason: $(head -1 "$KILL_FILE")"
  exit 1
fi

if ! HITS=$(gh issue list -R "$REPO" --label "$STOP_LABEL" --state open \
              --json number,title --jq '.[] | "#\(.number) \(.title)"' 2>&1); then
  echo "STOPPED: cannot read the stop state from GitHub, halting: $HITS"
  exit 1
fi

if [ -n "$HITS" ]; then
  echo "STOPPED: an open issue carries $STOP_LABEL"
  echo "$HITS" | sed 's/^/  /'
  exit 1
fi

echo "STOP_CHECK_OK: no kill file, no $STOP_LABEL issue"
exit 0
