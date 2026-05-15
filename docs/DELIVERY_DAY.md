# Delivery day runbook

For Sunday 2026-05-17, when the LewanSoul MaxArm physically arrives.
Total target time from unbox to first attested move: **30 minutes**.

The repo state going into delivery day is a fully-working sim plus
every plumbing line the real arm needs. You don't need to write any
new code on Sunday — only measure, calibrate, and flip a switch.

---

## What's already in place

| Piece | Where | Status |
|---|---|---|
| 4-DOF sim with FK/IK ported from firmware | `src/armsim.rail` | ✅ shipping |
| AA 55 byte protocol (SET_ANGLE, SET_XYZ, SET_PWMSERVO, SET_SUCTIONNOZZLE, READ_ANGLE, READ_XYZ) | `protocols/lewansoul.rail` | ✅ 9/9 self-tests PASS |
| Browser viewer with smooth motion easing + chain panel + ghost arm | `web/index.html` | ✅ shipping |
| Per-action attestation chain at `~/.ledatic-arm/chain/` | `src/armsim.rail` (`/chain`) | ✅ shipping |
| Beacon anchor via fleet0 witness | `/anchor` route | ⚠ Pi witness reachable; `/sign` returns empty (sign_token mismatch — fix in a follow-up) |
| Programs: wave / nod / scan / dance | `programs/`, `tools/program_runner.sh` | ✅ shipping |
| E-stop + z-floor + reach-out-of-workspace gating | `src/armsim.rail` | ✅ shipping |
| Bridge env vars (FIFO / TCP modes) | `src/armsim.rail` | ✅ shipping (sim-verified) |
| `tools/enable_bridge.sh` — one-command go-real | `tools/enable_bridge.sh` | ✅ shipping |

The 12-question pre-delivery research from `docs/MAXARM_REFERENCE.md`
seeded all of the above. Open questions that remain require the
physical arm in hand to resolve.

---

## Step 1 — physical inspection (5 min)

- Photograph the wiring before powering on. Any visible cable strain
  between the carbon-fiber upright and the base column will show now.
- Verify the **12V / 5A** barrel-jack adapter is in the box and
  center-positive.
- Confirm the suction nozzle / cup is connected to the air line.
- Confirm the included **USB-C cable** for serial control. Not all
  shipments include one — if missing, fall back to WiFi/BLE on day-of.

## Step 2 — caliper measurements (5 min)

Verify firmware-canonical link lengths against the physical arm.
Update `arms/maxarm.json` if any are off by more than 0.5 mm.

| Symbol | Expected (mm) | What to measure |
|---|---|---|
| L0 | 84.4 | Base plate top to shoulder pivot (vertical) |
| L1 | 8.14 | Base axis to shoulder pivot (horizontal offset) |
| L2 | 128.4 | Shoulder pivot to elbow pivot (straight line) |
| L3 | 138.0 | Elbow pivot to wrist pivot (straight line) |
| L4 | 16.8 | Wrist pivot to suction-tip (horizontal at home) |

## Step 3 — power-on (3 min)

- Plug in 12V adapter. Arm makes a brief servo-init sound, settles at
  factory "ready" pose (not necessarily the firmware home).
- LEDs: confirm power LED is solid. Bluetooth LED (GPIO26) should
  blink advertising `MaxArm`.

## Step 4 — pick a transport (5 min)

The sim drives the real arm via one of three transports. Pick whichever
is reachable on Sunday:

### USB-serial (recommended — lowest latency)
1. Plug USB-C cable into the arm.
2. Find the device: `ls /dev/cu.usbserial-* /dev/cu.usbmodem*`
3. Run: `tools/enable_bridge.sh usb /dev/cu.usbserial-XXXX`
   - Auto-detects if you omit the device path.
   - Creates `/tmp/armsim_bridge.fifo`, starts a `socat` relay to the
     serial device, sets `ARMSIM_BRIDGE=fifo`, restarts the server.
4. Verify: `tools/enable_bridge.sh status` shows `bridge env: fifo`
   and the relay PID.

### WiFi
1. Connect this machine to the arm's AP: SSID prefix `HW...`, password
   `hiwonder`. (Or, if the arm is in STA mode, look it up on your
   router.)
2. Sniff the port: `nmap -p 6000,8888,9000,23,80 192.168.149.1`.
   Update `arms/maxarm.json.controller._wifi_port` with the result.
3. Run: `tools/enable_bridge.sh wifi 192.168.149.1 <port>`

### BLE
Out of scope for the one-command path. If only BLE is available:
1. Connect via nRF Connect; dump GATT services.
2. Note the Nordic UART Service (NUS) characteristic UUIDs.
3. Write a small Python relay that reads from `/tmp/armsim_bridge.fifo`
   and writes to the BLE TX characteristic. Then:
   `ARMSIM_BRIDGE=fifo ./run.sh 7071`.

## Step 5 — protocol smoke (5 min)

With bridge enabled:

```bash
# Confirm the byte builder matches what the real arm wants.
cd ~/projects/rail
./rail_native run ~/projects/ledatic-arm/protocols/lewansoul.rail
# expect: === 9/9 passed ===
```

Then drive one slow move from the viewer:
1. Open <http://localhost:7071/>
2. Slide p2 (shoulder) by ~50 pulses. Watch the real arm follow.
3. If it doesn't move:
   - `tools/enable_bridge.sh status` — confirm relay is running.
   - `tail -f /tmp/armsim_bridge_relay.log` — look for serial errors.
   - `tail -f /tmp/armsim_serve.log` — confirm /pose received.
4. If it moves but jerkily: bump `ARMSIM_DEFAULT_MOVE_MS` in
   `.bridge.env` from 800 to 1200, restart server.

## Step 6 — calibration captures (10 min)

Drop into `arms/maxarm.json._calibration_log` as you measure:

1. **J1 cable-bind limit.** Sweep `p1` 0→1000 in 50-pulse steps. Find
   the first physical resistance from the wiring loom. Record as a
   soft limit narrower than the firmware's 0–1000.
2. **Servo offsets.** `BusServo.get_offset(id)` for id=1,2,3 returns
   the factory per-servo trim. Record all three.
3. **VIN at idle vs load.** `BusServo.get_vin(id)` returns mV. Confirm
   ~11.1V at idle, no sag below 10V under motion.
4. **Pump rail.** Multimeter at M1 connector while `/suction?cmd=on`
   is active. Expect ~6V.
5. **AA 55 round-trip latency.** Time a `READ_ANGLE` send + response
   over your chosen transport. USB: 5–15 ms. BLE: 50–200 ms.
6. **Endianness sanity.** `curl /reach?x=0&y=-150&z=180` — arm should
   move to that XYZ. Then `curl /reach?x=80&y=-100&z=120` — different
   pose. Verify signs match the world frame (X right, Y forward).

## Step 7 — first attested move (3 min)

```bash
# Capture the pre-physical chain head as a milestone marker.
curl http://localhost:7071/state | jq -r .chain.head > /tmp/pre_physical_head.txt
echo "pre-physical chain head: $(cat /tmp/pre_physical_head.txt)"

# Send the first move through the bridge.
curl "http://localhost:7071/home?time_ms=2000"

# Verify the chain advanced and the arm physically moved.
curl http://localhost:7071/state | jq '.chain, .pulses'

# Anchor the milestone to the public beacon (if the witness sign-token
# rotation has been resolved by Sunday).
curl http://localhost:7071/anchor | jq
```

If the anchor succeeds, the very first physical motion of this arm is
now signed against the ledatic.org entropy beacon and replayable by
anyone. Tag this in the repo:

```bash
cd ~/projects/ledatic-arm
git tag -a v0.1-physical -m "First physical move at $(date -u +%FT%TZ)"
git push --tags
```

## Step 8 — try the programs (5 min)

Sanity-check each routine against the real arm. **First run each at
0.5× speed** by bumping `ARMSIM_DEFAULT_MOVE_MS` to ~1500 before
starting:

```bash
ARMSIM_DEFAULT_MOVE_MS=1500 ./run.sh 7071  # or edit .bridge.env
```

Then in the viewer's PROGRAMS panel, click RUN on each in turn:
1. `nod` — shoulder dip. Safest first test.
2. `wave` — base sweep. Tests J1 over a large range.
3. `scan` — slow sweep. Tests J1 with continuous motion.
4. `dance` — full combo. Only after the above three are clean.

If anything looks off:
- Hit **STOP** in the panel (`/program/stop`).
- Hit **HOME** to recenter.
- Inspect `/chain` to see the last commanded pulses.

If a servo gets stuck or hot: **`/estop`** immediately. Power-cycle.

## Red flags

- Servo whine that doesn't stop after init → a bus servo is binding;
  cut power, reposition, do not retry without inspecting.
- Pump runs continuously without a `/suction?cmd=on` → vent valve
  miswired or H-bridge stuck. Cut power.
- Z drift over minutes without commands → EEPROM offset may be
  corrupting. Record offsets, factory-reset the servos.
- Latency > 500 ms on USB → wrong baud. Outer protocol is 9600, NOT
  115200 (which is the inner bus-servo rate).
- Viewer's ghost arm pulls noticeably away from the solid arm → the
  real position differs from commanded by > 2 mm. Mechanical slack,
  calibration drift, or under-voltage.

## Stop / disable

```bash
tools/enable_bridge.sh off            # back to pure sim
./run.sh 7071                          # restart in sim mode after
```

`.bridge.env` is `.gitignore`'d — your local bridge config never
leaves this machine.

## After delivery — open follow-ups

- Resolve fleet0 witness sign_token rotation so `/anchor` produces
  signed attestations again (currently returns "witness signer
  returned nothing").
- `/actual` route currently returns `/state` verbatim. With the bridge
  on, wire a periodic `READ_ANGLE` poll and have `/actual` reflect the
  arm's reported pulses instead.
- Once `/actual` is real, the viewer's ghost arm will show physical
  slack/error against the commanded pose. Watch for >5 mm drift as a
  calibration trigger.
- Behavior-cloning policy net on top of Rail GPU kernels. Once the
  arm reliably executes `/pose` requests, scripted demonstration
  trajectories become training data.
