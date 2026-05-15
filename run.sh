#!/usr/bin/env bash
# armsim launcher -- compile + serve the MaxArm sim.
# Auto-detects rail_native via RAIL_BIN env or ~/projects/rail/rail_native.
set -euo pipefail

PORT="${1:-7071}"
HERE="$(cd "$(dirname "$0")" && pwd)"

RAIL_BIN="${RAIL_BIN:-$HOME/projects/rail/rail_native}"
RAIL_ROOT="${RAIL_ROOT:-$(dirname "$RAIL_BIN")}"

if [[ ! -x "$RAIL_BIN" ]]; then
  echo "armsim: rail_native not found at $RAIL_BIN" >&2
  echo "armsim: set RAIL_BIN, or clone github.com/zemo-g/rail at ~/projects/rail" >&2
  exit 1
fi
if [[ ! -d "$RAIL_ROOT/stdlib" ]]; then
  echo "armsim: stdlib/ not found at $RAIL_ROOT/stdlib" >&2
  echo "armsim: set RAIL_ROOT to the rail repo root" >&2
  exit 1
fi

echo "armsim: compiling $HERE/src/armsim.rail with $RAIL_BIN"
( cd "$RAIL_ROOT" && "$RAIL_BIN" "$HERE/src/armsim.rail" )
cp /tmp/rail_out /tmp/armsim_handler
chmod +x /tmp/armsim_handler

cp "$HERE/web/index.html" /tmp/armsim_index.html

# Seed state at home pose if missing.
if [[ ! -f /tmp/armsim_state.txt ]]; then
  printf "500\n500\n500\n1500\n0\n" > /tmp/armsim_state.txt
fi

# Seed a few starter poses if missing.
if [[ ! -f /tmp/armsim_poses.txt ]]; then
  cat > /tmp/armsim_poses.txt <<POSES
home 500 500 500 1500 0
park 500 700 470 1500 0
reach-front 500 500 700 1500 0
reach-down 500 700 700 1500 0
POSES
fi

echo "armsim: handler $(stat -f %z /tmp/armsim_handler 2>/dev/null || stat -c %s /tmp/armsim_handler) bytes"
echo "armsim: serving on :${PORT}  ->  http://$(hostname -s):${PORT}/"

exec python3 "$HERE/serve.py" "$PORT"
