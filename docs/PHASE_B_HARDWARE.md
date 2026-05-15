# armsim Phase B — Hardware control

Design-only doc. No code yet. Phase A (sim, Mini) is shipping; this is the
follow-on that puts real servos behind the same HTTP contract.

## Goal

The browser UI, the Rail handler, and every consumer of the HTTP API stay
identical. Only the back end changes: `/pose`, `/reach`, `/poses/load`, and
`/home` actuate real servos instead of just updating a state file.

Sim and hardware should be the same surface area, swappable with a flag.

## What's physically on the desk

Inherited from the legacy stack (`~/Desktop/eezybotarm/`, `CALITEST.ino`,
`~/Documents/Arduino/grokgarm/`):

- **EEZYbotARM MK2**, 3D-printed, parallelogram linkage
- **3× MG995 servos** (base, shoulder, elbow); gripper not wired yet
- **PCA9685** 16-channel PWM driver, default I²C address 0x40
  - Channels: base=8, shoulder=9, elbow=10 (gripper reserved at 11)
- **Arduino** (Uno or Nano clone) connected over USB serial @ 115200
- **5V external power** to PCA9685 V+ (MG995 stalls > 1 A — do NOT feed
  from USB)

The arm is currently on Air's desk. Air has USB.

## Architecture options

**Option A — Move armsim to Air, point it at serial.**
One process, one HTTP server, one binary. Same code path for sim + hw via
mode flag. *But*: Air has no rail_native — every build round-trips through
Mini, and Air is the satellite (offline more often). The bus factor of
"Air's USB cable" should be lower than "the brain stops working."

**Option B — Keep armsim on Mini; add `armd` on Air.**
`armsim` (Mini) is the public HTTP face; `armd` (Air) holds the serial port.
Mini's `/pose` forwards to Air over the tailnet. Two processes, two
machines, but each has a clear single role.

**Option C — `armd` on Air is the only thing; sim becomes a mode of it.**
Replace Mini's armsim with `armd` on Air. Sim and hardware are the same
program, picked by `--mode sim|hw`. Air gains the Rail toolchain (build on
Mini, scp the binary). Air becomes the home of the arm permanently.

**Recommendation: C.** Air is the arm's natural home — the cable, the
plug, the bench. Build pipeline is `Mini compiles → scp → Air runs`,
which already works for the existing `rail_native linux` cross-compile
flow (per `~/CLAUDE.md`). The sim doesn't need to live on Mini.

Until C is built, A or B can serve as bridges. The HTTP contract stays
stable through all of them.

## Firmware contract — line-JSON over 115200 baud

Replace the three legacy sketches (`eezybotarm.ino` (broken),
`CALITEST.ino`, `grokgarm.ino`) with a single `arm.ino`:

**Host → Arduino** (one JSON object per line, newline-terminated):

```
{"t":"pose","j":[B,S,E,G]}     // joint angles in degrees, int
{"t":"home"}                    // recenter
{"t":"ping"}                    // liveness
{"t":"cal","cmd":"b+"}          // CALITEST tuner — b+/b-/s+/s-/e+/e-/c
```

**Arduino → Host** (also line-JSON):

```
{"t":"ok","j":[B,S,E,G]}        // command accepted, current joints
{"t":"pong","up_ms":12345}      // heartbeat
{"t":"err","msg":"..."}         // anything off-nominal
```

- Hard mechanical limits compiled into firmware (last-line defense).
- Tuner mode behind `#define TUNER_MODE` — bench calibration without daemon.
- Servo→joint mapping (the parallelogram offset / inversion / scaling) is
  in firmware, NOT in the daemon. Daemon sends joint angles; firmware
  maps to PCA9685 PWM ticks. Calibration burns into firmware.

Why line-JSON over the legacy fixed `"Bxxx Sxxx Exxx"` 14-byte format:
- Survives adding the gripper (4th joint already in the schema).
- Parses with Rail's `stdlib/json.rail`.
- Self-describing for debugging — a serial monitor shows what's flowing.
- ~30 bytes per pose at 115200 baud = ~3.5 KB/s. Plenty of headroom.

## Serial library choice

Three plausible paths:

1. **Rail FFI to `libserialport`.** Cleanest. `libserialport` is BSD-licensed
   cross-platform, MacPorts/Homebrew package. Wrap `sp_open_port`,
   `sp_blocking_read`, `sp_blocking_write` as `foreign`. Maybe 60 lines of
   FFI.
2. **Shell out to `screen` / `cu` / Python `pyserial`.** Quick but blocks
   the Rail loop on subprocess each command. Latency overhead unacceptable
   for smooth motion (each /pose sends one packet; subprocess spawn alone
   is 10–30 ms).
3. **Tiny C glue (`armserial.c` → libarmserial.dylib).** Build a static
   helper that exposes 4 functions — open/close/read_line/write_line —
   and link via `foreign`. Easier than wrapping all of libserialport.

**Recommendation: 3** for the first cut. ~80 lines of C, no platform
abstraction we don't need. Easy to swap for option 1 later.

## Smooth motion (the real version)

In sim, the browser interpolates between server snapshots. With hardware,
the daemon must interpolate. Servo commands at ~50 Hz (every 20 ms) with
a max joint velocity (deg/s) — start conservative, e.g. 60 deg/s — and a
trapezoidal velocity profile.

Per `/pose` request:
1. Capture current pose
2. Compute time-to-target = max(|Δjoint| / max_vel) for each joint
3. Step at 20 ms ticks, lerping joints with ease-out
4. Each tick writes `{"t":"pose","j":[...]}` to the serial line
5. Return HTTP 200 on completion (or 202 immediately + status endpoint?)

Smooth motion is the single biggest hardware-safety feature. Servos
under MG995 stall current love a sudden command; ramped commands draw
less current and last longer.

## Calibration handoff

The three legacy sketches have three disagreeing calibration tables —
they're servo-frame, not joint-frame. The Rail sim picks ±90° as
placeholders. Phase B needs a real calibration session:

1. Wire arm + power + Arduino
2. Flash `arm.ino` with `TUNER_MODE` enabled
3. Use the existing CALITEST b+/s+/e+/c commands to find joint extremes
4. Record PWM tick values for the limits
5. Burn into firmware as `JOINT_PWM_MIN[3]`, `JOINT_PWM_MAX[3]`
6. Re-flash without TUNER_MODE
7. `arm.json` exposes the joint-frame limits to the daemon

## Safety primitives to ship with Phase B

- **E-stop endpoint**: `POST /estop` — daemon writes a "go to safe pose"
  command and refuses further /pose until `/clear`. Browser exposes a
  big red button.
- **Velocity cap** in the daemon, enforceable per-joint.
- **Software floor**: refuse poses where computed tool z < 0 (under the
  table).
- **Watchdog**: Arduino expects a pose or ping every N seconds, otherwise
  reverts to last known good pose. (Optional. Mostly matters if the
  daemon crashes mid-motion.)
- **Reachability gate**: /reach already returns `reachable=0` for
  unreachable targets — Phase B should also gate on joint-limit-after-
  clamp to flag "reachable in 3D but not within joint envelope".

## Open questions

- Gripper geometry — needs a CAD search of the EEZYbotARM remixes; the
  base remix STL in Downloads might include one.
- Servo replacements — the MG995s are old; consider upgrading to MG996R
  or digital servos before any long calibration session.
- Hardware mode + sim mode in one binary — or two? (Recommendation: one
  binary, `--mode hw|sim`, with `--port /dev/cu.usbmodem*` for hw.)
- Where does the arm physically live long-term? If permanently on Air,
  option C is locked in. If it migrates to Mini's bench, option B.

## Concrete first steps when this becomes the active project

1. Take inventory: open the box, photograph the wiring, confirm Arduino
   model, USB cable health, PCA9685 board variant.
2. Write `arm.ino` with TUNER_MODE; flash; do a fresh calibration.
3. Stub `armd.rail` on Air (just FK + state, no serial) — proves the
   build/deploy pipeline.
4. Add `armserial.c` glue + Rail FFI; smoke test by toggling one servo.
5. Wire `/pose` to actually send servo commands. Manually test from `arm`
   CLI.
6. Add velocity-limited interpolation in the daemon.
7. Flip browser URL from sim to daemon — same UI, real arm moves.
