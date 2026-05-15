# Delivery day runbook

The first 30 minutes after the LewanSoul MaxArm arrives, in order. Goal:
unbox -> measure -> calibrate -> first attested move, with the same
codebase that's been driving the sim.

## Pre-arrival state (what's already in this repo)

- `arms/maxarm.json` -- firmware-canonical geometry (link lengths, joint
  limits, servo IDs, pulse ranges). Authored from Hiwonder's published
  source.
- `src/armsim.rail` -- 4-DOF sim with FK/IK byte-ported from `_espmax.cpp`.
  Hash-chained attestation of every mutating action.
- `protocols/lewansoul.rail` -- byte-level AA 55 protocol module
  (SET_ANGLE, SET_XYZ, SET_PWMSERVO, SET_SUCTIONNOZZLE, READ_ANGLE,
  READ_XYZ). 9/9 self-tests PASS against documented frames.
- `web/index.html` -- 3D viewer + control panel.
- `docs/MAXARM_REFERENCE.md` -- protocol + geometry + open questions.

The sim is the same surface area the real arm will sit behind. Same
routes, same state contract.

## Step 1 -- physical inspection (5 min)

- Photograph the wiring (any cable damage between the carbon-fiber
  upright and the base column will show here).
- Verify the 12V/5A barrel-jack adapter is in the box and matches the
  arm's input (center-positive).
- Check the suction nozzle / cup is connected to the air line; the line
  should reach the pump module without slack tension.
- Confirm the included USB-C cable (for serial control) -- not all
  shipments include one.

## Step 2 -- caliper measurements (5 min)

Verify firmware-canonical values against the physical arm. Update
`arms/maxarm.json` if any are off by more than 0.5 mm.

| Symbol | Value (mm) | What to measure |
|---|---|---|
| L0 | 84.4 | Top of base plate to shoulder pivot (vertical) |
| L1 | 8.14 | Base axis to shoulder pivot (horizontal offset) |
| L2 | 128.4 | Shoulder pivot to elbow pivot (straight line) |
| L3 | 138.0 | Elbow pivot to wrist pivot (straight line) |
| L4 | 16.8 | Wrist pivot to suction tip (horizontal at home) |

## Step 3 -- power-on and BLE scan (5 min)

- Plug in 12V adapter. The arm should make a brief servo-init sound,
  then come to rest at "ready" pose (not necessarily the firmware home).
- On phone, open nRF Connect. Scan for `MaxArm` advertisement.
- Record the BLE GATT service + characteristic UUIDs. Likely Nordic UART
  Service (NUS):
  - Service: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
  - TX char: `6E400002-...`  (host writes here)
  - RX char: `6E400003-...`  (host reads notifications here)
- Capture an actual SET_ANGLE frame from Hiwonder's official app (if
  installed) over BLE to confirm it matches the AA 55 format.

## Step 4 -- WiFi sniff (5 min)

If the arm boots in AP mode (default for Hiwonder products):

```bash
# Connect to the arm's AP (SSID prefix HW..., pass: hiwonder)
nmap -p 6000,8888,9000,23,80 192.168.149.1
```

Record which port answers. Update `arms/maxarm.json.controller._wifi_port`.

If the arm is in STA mode, find its IP via the router DHCP table or
`avahi-browse -art` (it likely advertises mDNS as `maxarm.local`).

## Step 5 -- protocol smoke test (5 min)

With the arm reachable on USB or BLE:

```bash
# Confirm the protocol module's byte frames match what the arm wants.
cd ~/projects/ledatic-arm
./run.sh 7071    # sim still serves alongside
```

In a second terminal, compile the protocol self-tests:
```bash
cd ~/projects/rail
./rail_native run ~/projects/ledatic-arm/protocols/lewansoul.rail
# Expect: === 9/9 passed ===
```

Then send a single SET_ANGLE frame to the arm over USB-serial:
```bash
# Build the frame in Python first (mirrors what lewansoul.rail emits):
python3 -c "
import struct
def chk(d): return (~(sum(d))) & 0xFF
p1, p2, p3, t = 500, 500, 500, 2000  # home pulses, 2s
data = struct.pack('<HHHH', p1, p2, p3, t)
hdr = bytes([0xAA, 0x55, 0x01, len(data)])
print((hdr + data + bytes([chk([0x01, len(data)] + list(data))])).hex())
" | xxd -r -p > /tmp/set_angle.bin

# Send via stty + cat:
stty -f /dev/cu.usbmodem* 9600 raw
cat /tmp/set_angle.bin > /dev/cu.usbmodem*
# Arm should move to (500, 500, 500). Verify physical position.
```

## Step 6 -- bridge sim to arm (5 min)

Once protocol smoke passes, point the sim at the real arm. (This route
doesn't exist yet -- it's the next code slice. Tracking issue: when env
var `ARMSIM_AA55_HOST` is set, fork every mutating route into an AA 55
frame via the protocol module.) Until then, drive the arm directly with
small Python scripts using the byte frames from
`protocols/lewansoul.rail`.

## Step 7 -- first attested move (3 min)

```bash
# Wait for fleet0 witness to come back online (Pi may need power-cycle):
curl http://100.87.231.45:9102/health
# expect: {"ok":true,"name":"fleet0"}

# Then anchor the current chain head to the public beacon:
curl http://localhost:7071/anchor
# expect: {"ok":true,"head_sha":"...","head_idx":N,"attestation":{...}}

# This signs (state, action, model_hash, pulse_id) via the same Pi
# witness used for Rail release attestations.
```

The arm's first move under attestation is now in the public chain.
Verify by re-deriving the sha:

```bash
# Pull the chain entry that corresponds to the move you want to verify:
curl http://localhost:7071/chain | jq '.[N-1]'
# Re-derive sha:
printf '%s' '<prev_sha>|<t>|<kind>|<params>|<state>' | shasum -a 256
# Must match the entry's sha field byte-for-byte.
```

## Calibration captures (the 8 still-open questions)

Record into `arms/maxarm.json._calibration_log`:

1. Cable-bind limit on J1 (sweep `pulse=0->1000` in 50-pulse steps, find
   the first physical resistance).
2. `BusServo.get_offset(id)` for each of the 3 bus servos (factory
   per-servo offset, used by the firmware).
3. `BusServo.get_vin(id)` at idle and under load (validates 12V rail).
4. Multimeter at the M1 connector under pump-on -- expect ~6V at 100%
   duty.
5. AA-55 round-trip latency: time a `READ_ANGLE` request + response over
   USB-serial. Expect 5-15 ms. Over BLE expect 50-200 ms.
6. Verify endianness on `SET_XYZ` with one positive and one negative
   coordinate.
7. Confirm whether firmware ACKs every Style-A command or only reads.
8. Action-group `.rob` file format and the upload flow to ESP32 flash.

## Red flags to watch for

- Servo whine that doesn't stop after first init -- a bus servo may be
  stuck against the cable loom; cut power immediately and reposition.
- Pump runs continuously when no `/suction?cmd=on` sent -- vent valve
  may be miswired or the H-bridge channel is stuck. Cut power.
- Z drift over time without commands -- some bus servos have an EEPROM
  offset that can corrupt; record `get_offset` and reset to factory.
- Latency >500 ms over USB -- check serial baud (must be 9600 for the
  outer protocol, not 115200 like the inner bus).

## Once stable

- Commit measured `maxarm.json` updates (the `_calibration_status` and
  `_calibration_log` fields).
- Tag the repo `v0.1-physical`.
- Anchor the chain head one more time to lock the milestone in the
  public beacon.
