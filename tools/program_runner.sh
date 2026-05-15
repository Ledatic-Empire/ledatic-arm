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

  # send pose
  curl -sS --max-time 3 "${BASE}/pose?p1=${p1}&p2=${p2}&p3=${p3}&p4_us=${p4_us}&time_ms=${time_ms}" > /dev/null || {
    echo "program_runner: pose request failed at step $step_idx (server down?)" >&2
    exit 3
  }

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
