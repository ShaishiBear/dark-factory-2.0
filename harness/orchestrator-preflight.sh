#!/usr/bin/env bash
# Single protected entry point the external VPS orchestrator calls before queue selection.
# Exit non-zero means do not dispatch anything.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACTIVE_TTL="${FACTORY_ACTIVE_LEASE_TTL:-21600}"
LEGACY_TTL="${FACTORY_LEGACY_LEASE_TTL:-86400}"

cd "$ROOT"

# Order is load-bearing: if stop state is active OR unreadable, do not mutate queue state.
if ! bash scripts/factory-stop.sh; then
  echo "ORCHESTRATOR_PREFLIGHT_STOPPED: emergency stop active or unreadable" >&2
  exit 1
fi

# Only after a readable/clear stop state may stale claims be deterministically reaped.
if ! python3 scripts/factory_lease.py reap \
    --active-ttl "$ACTIVE_TTL" --legacy-ttl "$LEGACY_TTL"; then
  echo "ORCHESTRATOR_PREFLIGHT_STOPPED: lease reaper failed" >&2
  exit 1
fi

echo "ORCHESTRATOR_PREFLIGHT_OK active_ttl=$ACTIVE_TTL legacy_ttl=$LEGACY_TTL"
