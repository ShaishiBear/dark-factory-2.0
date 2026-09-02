#!/usr/bin/env bash
# Run one validation-host observation with the external sandbox environment.
# Secret VALUES are sourced into the child environment and are never printed or written.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VALIDATION_ENV="${DARK_FACTORY_VALIDATION_ENV:-/opt/dark-factory/validation.env}"
OBSERVATION_DIR="${FACTORY_OBSERVATION_DIR:-/opt/dark-factory/observations}"

if [ ! -f "$VALIDATION_ENV" ]; then
  echo "OBSERVATION_REFUSED: validation env missing at $VALIDATION_ENV" >&2
  exit 1
fi

# Never run an expensive observation while either emergency-stop mechanism is active.
if ! bash "$ROOT/scripts/factory-stop.sh"; then
  echo "OBSERVATION_REFUSED: factory stop is active or unreadable" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$VALIDATION_ENV"
set +a

mkdir -p "$OBSERVATION_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTPUT="${FACTORY_OBSERVATION_OUTPUT:-$OBSERVATION_DIR/observation-$STAMP.json}"

cd "$ROOT"
python3 harness/observe.py --output "$OUTPUT" "$@"
echo "VPS_OBSERVATION_FILE=$OUTPUT"
