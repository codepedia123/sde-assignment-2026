#!/usr/bin/env bash
# Contract: ./run.sh <SIM_URL> runs until the scenario finishes.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
STATE_DIR="${CONTROLLER_STATE_DIR:-.controller-state}"
exec python -m controller.main --url "$SIM_URL" --state-dir "$STATE_DIR"
