#!/usr/bin/env bash
# program_runner.sh -- play a MaxArm program file as a sequence of /pose
# and /suction HTTP calls to the local armsim.
#
# Program file format (one step per line):
#   <p1> <p2> <p3> <p4_us> <time_ms> <wait_ms> <suction>
#
# Where suction is "off", "on", "vent", or "-" (leave unchanged).
# Comments start with # and blank lines are ignored.
#
# Usage:  program_runner.sh <program.txt>
# Env:    ARMSIM_PORT=7071  ARMSIM_HOST=localhost
set -euo pipefail

PROGRAM="${1:?usage: program_runner.sh <program.txt>}"
PORT="${ARMSIM_PORT:-7071}"
HOST="${ARMSIM_HOST:-localhost}"
BASE="http://${HOST}:${PORT}"

if [[ ! -f "$PROGRAM" ]]; then
  echo "program_runner: file not found: $PROGRAM" >&2
  exit 1
fi

# A4 (C10) FAIL-CLOSED pre-flight gate. The canonical validator lives in
# armsim.rail (/validate_program runs the SAME clamp+FK+envelope_check that
# /pose actuates with -- no FK is re-implemented here). Refuse to actuate
# unless the server explicitly returns "ok":true. ANY other outcome (server
# down, non-200, malformed body, ok:false) aborts the run with exit 4.
PROG_NAME="$(basename "$PROGRAM" .txt)"
VALID="$(curl -sS --max-time 5 -w $'\n%{http_code}' "${BASE}/validate_program?name=${PROG_NAME}" 2>/dev/null || true)"
VALID_CODE="${VALID##*$'\n'}"
VALID_BODY="${VALID%$'\n'*}"
if [[ "$VALID_CODE" != "200" ]]; then
  echo "program_runner: PRE-FLIGHT FAILED (HTTP ${VALID_CODE:-none}, server down?), refusing to actuate: ${VALID_BODY}" >&2
  exit 4
fi
if ! printf '%s' "$VALID_BODY" | grep -q '"ok":true'; then
  echo "program_runner: PRE-FLIGHT FAILED, refusing to actuate: ${VALID_BODY}" >&2
  exit 4
fi

step_idx=0
while IFS= read -r raw || [[ -n "$raw" ]]; do
  # strip comments + trim
  line="${raw%%#*}"
  line="${line##[[:space:]]}"
  [[ -z "$line" ]] && continue
  step_idx=$((step_idx + 1))

  # parse 7 fields
  read -r p1 p2 p3 p4_us time_ms wait_ms suction <<< "$line"
  if [[ -z "${suction:-}" ]]; then
    echo "program_runner: malformed step $step_idx: $line" >&2
    exit 2
  fi

  # send pose -- check the HTTP STATUS, not just the connection. A below-floor
  # or out-of-envelope step returns 422 with a 200-fine connection (curl exit 0),
  # so checking only connection success would silently skip the rejected step
  # and KEEP actuating the rest. The pre-flight above should have caught it, but
  # this is the in-loop backstop: any non-2xx pose aborts the whole program.
  pose_code="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    "${BASE}/pose?p1=${p1}&p2=${p2}&p3=${p3}&p4_us=${p4_us}&time_ms=${time_ms}" 2>/dev/null || true)"
  if [[ -z "$pose_code" ]]; then
    echo "program_runner: pose request failed at step $step_idx (server down?)" >&2
    exit 3
  fi
  if [[ "$pose_code" != 2* ]]; then
    echo "program_runner: pose REJECTED at step $step_idx (HTTP $pose_code), aborting program" >&2
    exit 5
  fi

  # send suction if specified
  if [[ "$suction" != "-" && "$suction" != "off" ]]; then
    curl -sS --max-time 3 "${BASE}/suction?cmd=${suction}" > /dev/null || true
  elif [[ "$suction" == "off" ]]; then
    curl -sS --max-time 3 "${BASE}/suction?cmd=off" > /dev/null || true
  fi

  # wait for motion + dwell
  total_ms=$((time_ms + wait_ms))
  awk -v ms="$total_ms" 'BEGIN { system("sleep " ms / 1000) }'
done < "$PROGRAM"
