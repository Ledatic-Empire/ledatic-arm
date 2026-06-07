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
#   tools/enable_bridge.sh usb                # AA-55 firmware; auto-detect /dev/cu.usbserial-*
#   tools/enable_bridge.sh usb /dev/cu.usbserial-1240
#   tools/enable_bridge.sh repl               # factory MicroPython-REPL firmware (@115200) -> real MaxArm in hand
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
  # Persistent pyserial relay. It opens the serial port ONCE (a single ESP32
  # reset at startup) and reopens only the FIFO after each writer EOF, so the
  # bridge survives an unbounded number of /pose frames.
  #
  # socat was REMOVED (2026-06-07): `socat -u PIPE:$FIFO FILE:$dev` exits on the
  # first writer EOF, i.e. it carries exactly ONE frame then dies — the next
  # /pose blocks forever on the FIFO write and hangs the whole server. Wrapping
  # socat in a restart loop is worse: FILE:$dev reopens the port every frame,
  # re-toggling DTR and resetting the ESP32 on each move. Holding the serial fd
  # open in one long-lived process is the correct design.
  if ! python3 -c "import serial" >/dev/null 2>&1; then
    echo "relay needs pyserial:  python3 -m pip install pyserial   # then re-run" >&2
    exit 1
  fi
  local relay_py="/tmp/armsim_bridge_relay.py"
  cat > "$relay_py" <<'PY'
import sys, time, serial
DEV, BAUD, FIFO = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ser = serial.Serial(DEV, BAUD, timeout=1)
sys.stderr.write("relay: serial open %s @%d\n" % (DEV, BAUD)); sys.stderr.flush()
while True:
    try:
        with open(FIFO, "rb") as f:                       # blocks until a writer opens
            for chunk in iter(lambda: f.read(4096), b""):
                ser.write(chunk); ser.flush()
                sys.stderr.write("relay: fwd %d bytes %s\n" % (len(chunk), chunk[:16].hex()))
                sys.stderr.flush()
    except Exception as e:
        sys.stderr.write("relay err: %s\n" % e); sys.stderr.flush(); time.sleep(0.3)
PY
  nohup python3 "$relay_py" "$dev" "$baud" "$FIFO" > /tmp/armsim_bridge_relay.log 2>&1 &
  echo $! > "$RELAY_PID_FILE"
  echo "relay: persistent pyserial ($relay_py) FIFO=$FIFO -> $dev @ ${baud} (serial held open)"
}

write_env() {
  : > "$ENV_FILE"
  for kv in "$@"; do
    echo "$kv" >> "$ENV_FILE"
  done
  echo "env:  $ENV_FILE"
  cat "$ENV_FILE" | sed 's/^/      /'
}

start_repl_relay() {
  local dev="$1"
  kill_relay
  ensure_fifo
  # Translating relay for factory MicroPython-REPL firmware (@115200): parses the
  # sim's AA-55 frames off the FIFO and re-emits them as REPL commands
  # (arm.set_servo / arm.set_position). The AA-55 `usb` relay does NOT drive this
  # firmware (it ignores AA-55). See tools/repl_relay.py + memory
  # maxarm-firmware-reconciliation-2026-06-07.
  if ! python3 -c "import serial" >/dev/null 2>&1; then
    echo "relay needs pyserial:  python3 -m pip install pyserial   # then re-run" >&2
    exit 1
  fi
  nohup python3 "$REPO/tools/repl_relay.py" "$dev" 115200 "$FIFO" > /tmp/armsim_bridge_relay.log 2>&1 &
  echo $! > "$RELAY_PID_FILE"
  echo "relay: REPL translator ($REPO/tools/repl_relay.py) FIFO=$FIFO -> $dev @ 115200"
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
  repl)
    dev="${2:-$(find_usb_device || true)}"
    if [[ -z "$dev" ]]; then
      echo "no /dev/cu.usbserial-* found. plug in the MaxArm USB cable and re-run." >&2
      exit 1
    fi
    if [[ ! -e "$dev" ]]; then
      echo "device not found: $dev" >&2
      exit 1
    fi
    echo "device: $dev (factory MicroPython REPL @115200)"
    stop_server
    start_repl_relay "$dev"
    write_env \
      "ARMSIM_BRIDGE=fifo" \
      "ARMSIM_BRIDGE_FIFO=$FIFO" \
      "ARMSIM_BRIDGE_KIND=repl"
    start_server
    echo "ok. viewer poses/sliders/IK now drive the REAL arm via REPL (joints 1-3 + XYZ; wrist/suction = v2)."
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
