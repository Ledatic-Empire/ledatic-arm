#!/usr/bin/env bash
# read_arm_pose.sh -- query the MaxArm's actual joint pulses via the
# AA 55 READ_ANGLE function (function code 0x11). Prints
# "p1 p2 p3" (space-separated decimals) on success, exits 1 on failure.
#
# Usage:  read_arm_pose.sh <host> <port>
#
# Used by armsim.rail's /actual route in TCP bridge mode to populate
# the commanded-vs-actual ghost rendering in the viewer.
set -euo pipefail
HOST="${1:?usage: read_arm_pose.sh <host> <port>}"
PORT="${2:?usage: read_arm_pose.sh <host> <port>}"

# Send the READ_ANGLE frame (5 bytes: AA 55 11 00 EE), 1s connect+read
# timeout. Pipe response through xxd to a single hex stream.
hex=$(printf '\xAA\x55\x11\x00\xEE' | nc -w 1 "$HOST" "$PORT" 2>/dev/null \
        | xxd -p | tr -d '\n' || true)

# Expected response: 11 bytes (22 hex chars).
# Layout: AA 55 11 06 p1L p1H p2L p2H p3L p3H CHKSUM
if [[ ${#hex} -lt 22 ]]; then
  exit 1
fi

# Verify header
if [[ "${hex:0:6}" != "aa5511" && "${hex:0:6}" != "AA5511" ]]; then
  exit 2
fi

# Extract pulses (little-endian uint16)
p1=$(( 0x${hex:8:2} + (0x${hex:10:2} << 8) ))
p2=$(( 0x${hex:12:2} + (0x${hex:14:2} << 8) ))
p3=$(( 0x${hex:16:2} + (0x${hex:18:2} << 8) ))

echo "$p1 $p2 $p3"
