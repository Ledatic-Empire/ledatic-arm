#!/usr/bin/env bash
# armbridge_keepalive.sh -- watchdog that keeps the MaxArm USB bridge alive
# across Mac sleep/wake. Driven by the com.ledatic.armbridge LaunchAgent
# (RunAtLoad + StartInterval). IDEMPOTENT and conservative:
#
#   * Acts ONLY if the arm device is present AND .bridge.env is in fifo mode
#     (i.e. the user has explicitly enabled the bridge). `enable_bridge.sh off`
#     parks it; this watchdog then idles and will NOT auto-revive.
#   * Re-establishes the bridge ONLY when the relay or server is actually down
#     (e.g. after a sleep that killed the user processes). No churn when healthy.
#   * Sends NO arm commands. It only restarts the relay + HTTP server; the arm
#     holds whatever pose it already holds (servo torque is independent of the
#     bridge). It never issues a /pose, so it can't move or re-home the arm.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
DEV="${ARMBRIDGE_DEV:-/dev/cu.usbserial-210}"
PORT="${ARMSIM_PORT:-7071}"
ENV_FILE="$REPO/.bridge.env"
log() { echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"; }

# 1. Only act when the arm is actually plugged in.
[[ -e "$DEV" ]] || { log "arm $DEV absent — idle"; exit 0; }

# 2. Only act when the user has the bridge enabled (fifo mode).
grep -q '^ARMSIM_BRIDGE=fifo' "$ENV_FILE" 2>/dev/null \
  || { log "bridge not in fifo mode (.bridge.env) — idle"; exit 0; }

# 3. Which bridge kind is enabled? repl (real arm, MicroPython) vs usb (AA-55).
KIND="$(grep '^ARMSIM_BRIDGE_KIND=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
KIND="${KIND:-usb}"
RELAY_PAT="armsim_bridge_relay.py"; MODE="usb"
[[ "$KIND" == "repl" ]] && { RELAY_PAT="repl_relay.py"; MODE="repl"; }

# 4. Health check: relay process + HTTP server.
relay_up=0; pgrep -f "$RELAY_PAT" >/dev/null 2>&1 && relay_up=1
server_up=0; curl -s --max-time 3 "http://localhost:$PORT/state" >/dev/null 2>&1 && server_up=1

if [[ $relay_up -eq 1 && $server_up -eq 1 ]]; then
  exit 0   # healthy — do nothing (no ESP32 reset, no churn)
fi

# 5. Something's down — re-establish in the right mode (resets the ESP32 once).
log "bridge ($MODE) down (relay=$relay_up server=$server_up) — re-establishing on $DEV"
"$REPO/tools/enable_bridge.sh" "$MODE" "$DEV"
log "re-establish done"
