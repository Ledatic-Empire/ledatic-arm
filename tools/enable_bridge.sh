#!/usr/bin/env bash
# enable_bridge.sh -- one-command "go real" for ledatic-arm.
#
# Wires the sim's /pose route to a physical MaxArm via one of two paths:
#   usb  <device>      USB-serial cable to the arm's USB-C port
#   wifi <host> [port] TCP socket to the arm's WiFi listener
#
# Both paths use the AA 55 byte protocol that lewansoul.rail emits and
# armsim.rail already speaks via ARMSIM_BRIDGE=fifo|tcp. This script just
# sets the env, manages the FIFO + relay process for USB, and bounces the
# server.
#
# Usage:
#   tools/enable_bridge.sh usb                # auto-detect /dev/cu.usbserial-*
#   tools/enable_bridge.sh usb /dev/cu.usbserial-1240
#   tools/enable_bridge.sh wifi 192.168.149.1
#   tools/enable_bridge.sh wifi 192.168.149.1 6000
#   tools/enable_bridge.sh test               # fifo mode, no real arm; sniff with hexdump
#   tools/enable_bridge.sh off                # back to pure sim
#   tools/enable_bridge.sh status             # show current bridge state
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PORT="${ARMSIM_PORT:-7071}"
FIFO="${ARMSIM_BRIDGE_FIFO:-/tmp/armsim_bridge.fifo}"
RELAY_PID_FILE="/tmp/armsim_bridge_relay.pid"
ENV_FILE="$REPO/.bridge.env"

cmd="${1:-status}"

kill_relay() {
  if [[ -f "$RELAY_PID_FILE" ]]; then
    local pid
    pid=$(cat "$RELAY_PID_FILE" 2>/dev/null || true)
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    rm -f "$RELAY_PID_FILE"
  fi
}

stop_server() {
  pkill -f "serve.py ${PORT}" 2>/dev/null || true
  sleep 0.5
}

start_server() {
  cd "$REPO"
  if [[ -f "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
  fi
  ./run.sh "$PORT" > /tmp/armsim_serve.log 2>&1 &
  # Retry a few times — Rail compile + Python startup can stretch past 2s
  # on first run after a code change.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -s --max-time 2 "http://localhost:${PORT}/state" >/dev/null 2>&1; then
      echo "server: up on :${PORT}"
      return
    fi
  done
  echo "server: failed to come up — see /tmp/armsim_serve.log" >&2
  exit 1
}

show_status() {
  local bridge="off"
  if [[ -f "$ENV_FILE" ]]; then
    bridge=$(grep '^ARMSIM_BRIDGE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo off)
  fi
  echo "bridge env:  ${bridge}"
  if [[ -f "$RELAY_PID_FILE" ]]; then
    local pid
    pid=$(cat "$RELAY_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "usb relay:   running (PID $pid)"
    else
      echo "usb relay:   pidfile present but process dead"
    fi
  fi
  if curl -s --max-time 2 "http://localhost:${PORT}/bridge_status" 2>/dev/null | python3 -m json.tool 2>/dev/null; then
    : # output already printed
  else
    echo "server:      not reachable on :${PORT}"
  fi
}

ensure_fifo() {
  if [[ ! -p "$FIFO" ]]; then
    rm -f "$FIFO"
    mkfifo "$FIFO"
  fi
}

find_usb_device() {
  local devs
  devs=$(ls /dev/cu.usbserial-* /dev/cu.usbmodem* /dev/tty.usbserial-* 2>/dev/null || true)
  if [[ -z "$devs" ]]; then
    echo "" ; return 1
  fi
  echo "$devs" | head -1
}

start_usb_relay() {
  local dev="$1"
  local baud="${2:-9600}"
  kill_relay
  ensure_fifo
  # Prefer socat (persistent bridge across writer reopens). Fall back to a
  # Python loop that reopens the FIFO each iteration if socat is missing.
  if command -v socat >/dev/null 2>&1; then
    socat -u PIPE:"$FIFO" FILE:"$dev",b"$baud",cs8,parenb=0,cstopb=0,raw,echo=0 \
      > /tmp/armsim_bridge_relay.log 2>&1 &
    echo $! > "$RELAY_PID_FILE"
    echo "relay: socat FIFO=$FIFO -> $dev @ ${baud}"
  else
    nohup python3 - <<PY > /tmp/armsim_bridge_relay.log 2>&1 &
import os, sys, time
FIFO = "$FIFO"
DEV  = "$dev"
BAUD = $baud
try:
    import serial
except ImportError:
    print("python3 -m pip install pyserial    # then re-run enable_bridge.sh", file=sys.stderr)
    sys.exit(1)
ser = serial.Serial(DEV, BAUD, timeout=1)
while True:
    try:
        with open(FIFO, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                ser.write(chunk)
                ser.flush()
    except Exception as e:
        print("relay error:", e, file=sys.stderr)
        time.sleep(0.5)
PY
    echo $! > "$RELAY_PID_FILE"
    echo "relay: python FIFO=$FIFO -> $dev @ ${baud} (install socat for the lighter path)"
  fi
}

write_env() {
  : > "$ENV_FILE"
  for kv in "$@"; do
    echo "$kv" >> "$ENV_FILE"
  done
  echo "env:  $ENV_FILE"
  cat "$ENV_FILE" | sed 's/^/      /'
}

case "$cmd" in
  usb)
    dev="${2:-$(find_usb_device || true)}"
    if [[ -z "$dev" ]]; then
      echo "no /dev/cu.usbserial-* found. plug in the MaxArm USB cable and re-run." >&2
      exit 1
    fi
    if [[ ! -e "$dev" ]]; then
      echo "device not found: $dev" >&2
      exit 1
    fi
    echo "device: $dev"
    stop_server
    start_usb_relay "$dev"
    write_env \
      "ARMSIM_BRIDGE=fifo" \
      "ARMSIM_BRIDGE_FIFO=$FIFO"
    start_server
    echo "ok. /pose, /reach, /home, /poses/load, /suction will now actuate the real arm."
    ;;
  wifi)
    host="${2:?usage: enable_bridge.sh wifi <host> [port]}"
    port="${3:-6000}"
    stop_server
    kill_relay
    write_env \
      "ARMSIM_BRIDGE=tcp" \
      "ARMSIM_BRIDGE_HOST=$host" \
      "ARMSIM_BRIDGE_PORT=$port"
    start_server
    echo "ok. tcp bridge -> $host:$port"
    ;;
  test)
    stop_server
    ensure_fifo
    write_env \
      "ARMSIM_BRIDGE=fifo" \
      "ARMSIM_BRIDGE_FIFO=$FIFO"
    start_server
    echo "ok. sniff bytes with:  hexdump -C $FIFO"
    ;;
  off)
    stop_server
    kill_relay
    # Write an explicit ARMSIM_BRIDGE=off rather than just removing the
    # env file -- belt-and-suspenders against env leaks from the parent
    # shell or from prior test/usb/wifi invocations in the same session.
    write_env "ARMSIM_BRIDGE=off"
    unset ARMSIM_BRIDGE ARMSIM_BRIDGE_FIFO ARMSIM_BRIDGE_HOST ARMSIM_BRIDGE_PORT
    start_server
    echo "ok. pure sim mode."
    ;;
  status|*)
    show_status
    ;;
esac
