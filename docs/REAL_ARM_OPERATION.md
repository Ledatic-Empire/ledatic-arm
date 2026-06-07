# Driving the real MaxArm — operation, architecture, and the bring-up story

Everything learned getting the physical Hiwonder MaxArm driven from the
`ledatic-arm` viewer (2026-06-07). Read this before touching the hardware.

---

## TL;DR

- The arm runs **factory MicroPython REPL firmware @ 115200**, NOT the AA-55 binary
  protocol the sim/docs assume. Drive it with `arm.set_position(...)` /
  `arm.set_servo(...)` Python text.
- The viewer still drives it: `tools/repl_relay.py` translates the sim's AA-55
  frames into REPL commands. Bring up with **`tools/enable_bridge.sh repl`**.
- **If the arm "won't move" but the REPL says `(ok)`: the 12V servo rail is OFF.**
  The ESP32 runs on USB and accepts commands while the servos sit dead.
- Every command is **clamped** to safe joint ranges (`arms/safe_limits.json`) and
  **checksum-validated**; values are sent as **hex** (a Rail NUL bug eats `0x00`).

---

## 1. Firmware & wiring

| | |
|---|---|
| Controller | ESP32, factory **MicroPython REPL** firmware (decompiled from Hiwonder `MaxArm.app`) |
| Host link | USB-C on the ESP32 → **CH340** USB-UART (VID `0x1A86` / PID `0x7523`) → `/dev/cu.usbserial-*` |
| Baud | **115200** 8N1 (the REPL). *Not* 9600 — that's the AA-55 firmware variant the repo was written against, which this unit does NOT run. |
| Protocol | Python text over USB-CDC: `arm.set_position((x,y,z),dur)`, `\r\n`-terminated; responses are tuples + `>>> ` |
| Enumerate | The ESP32 needs **main power on** to appear on the Mac at all. |

The full firmware reconciliation (why the May "spurarm" notes said 9600/AA-55 and
this says 115200/REPL — they were two characterizations of the same unit, REPL is
correct) lives in the operator's memory note `maxarm-firmware-reconciliation-2026-06-07`.

## 2. Power (read this twice)

- **12V / 5A barrel-jack wall adapter** feeds the servo rail (11.1V nominal) AND
  the ESP32 logic.
- The single most confusing failure mode: **servo power off, ESP32 on.** Plugged
  into USB, the REPL answers every command with `(ok)` and `arm.read_position()`
  returns a value — but **nothing moves**, because the servos have no power.
- Confirm power from the REPL: `arm.bus_servo.get_vin(1)` → ~`12000` mV means on,
  ~`0`/`None` means off.
- The servos **hold torque** while loaded and heat up if left holding a static
  loaded pose — this is the thing that damages them. The relay now **auto-relaxes**
  them after `idle_relax_sec` (default 10s) of no movement (see §10), so they don't
  cook during long/unattended runs. To relax immediately: `arm.teaching_mode()`
  (limps all 3 bus servos; the arm sags, so support it). **Power down** to fully
  de-energize.

## 3. Architecture — how the viewer drives the real arm

```
browser viewer ──HTTP──> armsim.rail (Rail sim, computes FK/poses)
                              │ writes HEX-encoded AA-55 frames, one per line
                              ▼
                     /tmp/armsim_bridge.fifo
                              │
                   tools/repl_relay.py  ── parses hex, checksum-validates,
                              │            CLAMPS to safe ranges, translates:
                              │              SET_ANGLE    -> arm.set_servo(1..3,p,t)
                              │              SET_XYZ      -> arm.set_position((x,y,z),t)
                              │              SET_PWMSERVO -> nozzle.set_angle(deg,t)  (wrist)
                              │              SET_SUCTION  -> nozzle.on()/off()        (vacuum pump)
                              ▼
                  /dev/cu.usbserial-210 @ 115200  ── MicroPython REPL ── servos
```

The Rail sim, FK, poses, programs, and attestation chain are all unchanged — only
the **wire translation** differs from the AA-55 `usb` path. The `usb` (raw AA-55)
relay drives the *sim only*; it does nothing on this firmware.

## 4. Bring-up / operation

```bash
cd ~/projects/ledatic-arm
tools/enable_bridge.sh repl            # auto-detects /dev/cu.usbserial-*; sets repl mode
open http://localhost:7071/            # viewer; poses/sliders/IK now move the REAL arm
tools/enable_bridge.sh off             # back to sim-only (no relay)
tools/enable_bridge.sh status          # show bridge state
```

- **E-STOP**: the viewer's STOP button (or `Esc`) hits `GET /estop` → halts in-flight
  motion (`bus_servo.stop` via a HALT frame, func `0x7F`), drops suction, sets the
  estop flag (`/tmp/armsim_estop.txt`), and locks out every mutating route until
  `GET /clear`. `/state` reports `estop`; the servos hold (don't drop) then auto-relax.
- A LaunchAgent **`com.ledatic.armbridge`** (`tools/armbridge_keepalive.sh`) revives
  the bridge after a Mac sleep, in the right mode. It sends **no** arm commands.
- Opening the serial port **resets the ESP32** (DTR), so the arm re-inits whenever
  the bridge (re)starts.

## 5. Bugs found & fixed during bring-up (all committed)

| Bug | Symptom | Fix |
|---|---|---|
| **socat relay one-shot** | bridge carried exactly one frame then died; next `/pose` hung the server | persistent pyserial relay (holds serial open, reopens FIFO) |
| **no checksum check** | a mis-aligned frame decoded to garbage pulses and was actuated | validate `CHKSUM = ~(FUNC+LEN+ΣDATA)&0xFF`, drop bad frames |
| **NUL-byte drop** | any pulse < 256 (e.g. 200/250) corrupted into wild commands — **the real cause of the arm thrashing on programs** | Rail's `char_from_int(0)` returns an EMPTY string, so binary frames lose every `0x00`. Send **hex** over the FIFO, decode in the relay (mirrors the TCP path). |

The Rail bug is durable and bites any binary-over-strings code — see memory
`rail-bug-char-from-int-nul`.

## 6. Safety — clamp + operating ranges

Every translated command is clamped at the relay chokepoint, so no program,
slider, or IK target can over-drive a joint. Limits live in
**`arms/safe_limits.json`** (auto-loaded; edit + restart the bridge to change).

**Measured 2026-06-07** (`tools/arm_limits.py`, via `get_position` stall detection,
each joint jogged from home in isolation):

| Servo | Safe range | Notes |
|---|---|---|
| 1 base_yaw | **120 – 880** (pulse) | jogged free 86–912 with no stall — true loom limit is wider |
| 2 shoulder | **315 – 685** (pulse) | free 304–695; firmware also clamps > 700 |
| 3 elbow | **490 – 725** (pulse) | free 484–735; firmware also clamps < 470 |
| 4 wrist | **600 – 2400 µs** (≈ ±81°) | small LFD-01M PWM servo (`nozzle.set_angle`); open-loop, no feedback; swept ±80° clean |
| suction | `nozzle.on()` / `off()` | vacuum pump on / vent — no position, just on/off |
| XYZ box | x ±130, y −230..−90, z 120..260 | conservative box around ORIGIN (0,−163,212) |

⚠️ These are **per-servo** limits found with the other joints at home. They do
**not** guarantee combined-pose safety — the arm can still reach the table or
itself inside these ranges. To widen them or capture workspace/collision limits,
re-run `tools/arm_limits.py` (adjust the `CAPS`) **with a human watching**, since
`get_position` senses servo hard-stops but not soft collisions.

## 7. REPL API (confirmed via `dir(arm)` / `dir(arm.bus_servo)`)

```
arm.set_position((x,y,z), dur)   arm.set_position_with_speed   arm.set_position_relatively
arm.set_servo(id, pulse, dur)    arm.set_servo_with_speed      arm.set_servo_relatively
arm.set_joint(id, angle, dur)    arm.go_home()                 arm.teaching_mode()   # relax all 3
arm.read_position() -> (x,y,z)   arm.verify_position           arm.ORIGIN = (0,-163,212)
arm.bus_servo.run(id, pulse, dur)   .load(id) / .unload(id)    .get_position(id)
arm.bus_servo.get_vin(id) -> mV     .stop(id) / .set_mode      .get_offset / .save_offset
nozzle.set_angle(deg, dur)          # wrist (PWM nozzle servo), deg -90..90
nozzle.on() / nozzle.off()          # vacuum pump: suck / vent
nozzle.pump_f / pump_b / valve_f / valve_b   # pump + valve H-bridge primitives
```

## 8. Known gaps / v2

- **Relax button in the viewer** — no `/relax` route yet; relax is `teaching_mode()`
  over the REPL only.
- **Combined-pose / workspace collision limits** — not measured (needs supervised run).
- **No feedback to the viewer** — the bridge is one-way; the viewer shows the sim's
  commanded pose, not the arm's true `read_position`.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| arm not on `/dev/cu.*` at all | ESP32 unpowered, or a **charge-only USB-C cable** (no data lines) |
| REPL says `(ok)` but nothing moves | **12V servo rail off** — check `get_vin`; flip the power |
| relay log shows garbage pulses (e.g. -2822) | running an old relay without the hex/checksum fix — rebuild + restart |
| `BAD CKSUM` in relay log | corrupt/mis-aligned frame, correctly rejected |
| `CLAMP servoN ... -> ...` in relay log | a command exceeded the safe range and was bounded (working as intended) |

## 10. Long-term / unattended operation — auto-relax

The big HTS-35H bus servos (1-3) overheat if they HOLD a loaded pose. The relay
runs a watchdog that unloads them after `idle_relax_sec` (in `arms/safe_limits.json`,
default **10s**) with no joint move, and re-loads them automatically on the next
move. Net effect: the arm holds torque only while moving / briefly after, and rests
limp between movements — safe to leave running for hours.

- Verified live 2026-06-07: move → `AUTO-RELAX (idle >10s) -> teaching_mode` →
  next move → `LOAD (re-engage from relaxed)`.
- The arm **sags** when it auto-relaxes (servos go limp). By design — don't expect it
  to hold a high/loaded pose between commands.
- Tuning: raise `idle_relax_sec` for fewer sag cycles in slow sequences; lower it for
  cooler running; set `0` to disable (then YOU must relax or power down).
- The PWM wrist + vacuum pump are NOT auto-relaxed (small servo, no torque-hold heat;
  the pump stays as commanded so it won't drop a held part).
- The `com.ledatic.armbridge` keepalive keeps the bridge (with auto-relax) running
  across Mac sleep, so unattended operation survives a nap.

**Saved operating profile** — everything needed to run it safely is persisted in the
repo: **controls** (§3, §7), **limits** (§6 + `arms/safe_limits.json`), and the
**relax policy** (this section + `idle_relax_sec`). The arm comes up safe-to-run on a
plain `tools/enable_bridge.sh repl`.
