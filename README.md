# ledatic-arm

A Rail-native robotic arm stack — simulator, kinematics, motion control,
wire protocol, and per-action attestation. Same code drives the paper
sim and the physical arm.

## Status

**Sim functional, pre-delivery prep complete.** The single arm in scope
is the LewanSoul MaxArm (ESP32-based, 4-DOF + suction). Arrival
scheduled for 2026-05-17. The sim mirrors the firmware's FK/IK
byte-for-byte and speaks the same AA 55 wire protocol; flipping to
real-arm control is a single `tools/enable_bridge.sh` invocation.

## Quickstart (sim)

Requires a Rail compiler (`github.com/zemo-g/rail`).

```bash
git clone https://github.com/zemo-g/rail ~/projects/rail
# follow rail's README to produce a rail_native binary

git clone https://github.com/Ledatic-Empire/ledatic-arm ~/projects/ledatic-arm
cd ~/projects/ledatic-arm
./run.sh 7071
open http://localhost:7071/
```

The browser renders the 4-DOF MaxArm. Drive it via joint sliders, IK
targets, named poses, or canned programs (`wave`, `nod`, `scan`,
`dance`). Every move is hash-chained for tamper-evident audit.

## Go real (when the MaxArm is plugged in)

```bash
# USB-serial (lowest latency)
tools/enable_bridge.sh usb                        # auto-detect device
tools/enable_bridge.sh usb /dev/cu.usbserial-XXX  # explicit

# WiFi (TCP)
tools/enable_bridge.sh wifi 192.168.149.1 6000

# Back to pure sim
tools/enable_bridge.sh off

# What mode am I in?
tools/enable_bridge.sh status
```

`tools/enable_bridge.sh` writes `.bridge.env` (gitignored), manages a
FIFO → serial relay via `socat` (or Python+pyserial fallback), and
bounces the server. The viewer URL, routes, and contract don't change
— the arm just starts physically moving in response to /pose. See
[docs/DELIVERY_DAY.md](docs/DELIVERY_DAY.md) for the full unbox →
calibrate → first attested move runbook.

## Layout

```
arms/        per-arm config (geometry, joint limits, servo channels)
src/         Rail source (FK/IK, trajectory, HTTP server)
protocols/   wire protocols (ESP32 HTTP for MaxArm)
web/         browser viewer (plain HTML/CSS/JS)
docs/        phase docs, calibration notes, delivery runbook
```

## Why Rail

Every kernel the GPU runs and every byte the network sends is
Rail-emitted, replayable, and signed against the public entropy beacon
at <https://ledatic.org/entropy>. The arm makes that thesis physical:
every commanded pose carries an attestation tuple
`(state, action, model_hash, kernel_hash, beacon_pulse)`. Auditable
motion.

## Routes

| GET | Effect |
|---|---|
| `/` | viewer HTML |
| `/state` | current pulses + joint degrees + FK + chain head + motion plan |
| `/pose?p1=&p2=&p3=&p4_us=&time_ms=` | set joint pulses; smooth interp over `time_ms` (default 800) |
| `/reach?x=&y=&z=&time_ms=` (mm) | IK + clamp + smooth motion |
| `/nozzle?deg=&time_ms=` | set nozzle wrist angle |
| `/suction?cmd=on\|vent\|off` | control pump + valve |
| `/home?time_ms=` | reset to home with smooth motion |
| `/poses` | list named poses |
| `/poses/save\|load\|delete?name=` | manage poses |
| `/chain` | append-only attestation chain (every mutating action) |
| `/anchor` | beacon-anchor the chain head via fleet0 witness |
| `/anchors` | list of historical anchors |
| `/programs` | list available programs (in `programs/`) |
| `/program?name=X` | run program `X.txt` in the background (HTTP 202) |
| `/program/stop` | kill any running program |
| `/estop` / `/clear` | emergency stop + release (HTTP 423 on locked routes) |
| `/actual` | actual arm position (stub; identical to /state in sim mode) |
| `/bridge_status` | last AA 55 frame the bridge sent + mode |

## Programs

Each file in `programs/` is a sequence of poses played back via the
`tools/program_runner.sh` script. Format (one step per line):

```
# p1   p2   p3   p4_us  time_ms  wait_ms  suction
500  600  750  1500     800      100      off
250  600  750  1500     600      50       -
800  600  750  1500     600      50       on
```

`suction` is `on` / `vent` / `off` / `-` (leave unchanged).

Starters: `wave`, `nod`, `scan`, `dance`. Author your own — drop a
`.txt` file in `programs/`, it appears in `/programs` immediately.

## Roadmap

- [x] `arms/maxarm.json` — geometry/limits firmware-canonical from Hiwonder source
- [x] `src/armsim.rail` — FK/IK byte-ported from `_espmax.cpp`, HTTP handler, state, named poses
- [x] `web/index.html` — 3D viewer matching MaxArm geometry, smooth motion easing, chain panel, ghost arm
- [x] `protocols/lewansoul.rail` — AA 55 byte protocol, 9/9 self-tests PASS
- [x] Attestation chain — every mutating action SHA-chained at `/chain`, persistent at `~/.ledatic-arm/chain/`
- [x] Beacon anchor — `/anchor` invokes fleet0 witness (sign_token rotation in flight)
- [x] Motion timing — `?time_ms=` on every mutating route, smoothstep ease, viewer interpolates at 60fps
- [x] Programs — `wave`, `nod`, `scan`, `dance` runners + viewer panel
- [x] E-stop, z-floor, JSON escape, per-PID attest temp
- [x] Real-arm bridge — `tools/enable_bridge.sh usb|wifi|off`, FIFO + TCP modes
- [x] `docs/DELIVERY_DAY.md` — 30-minute integration runbook
- [ ] `/actual` route — wire to `READ_ANGLE` polling once bridge is live
- [ ] Behavior-cloning policy net on top of Rail GPU kernels

## License

[BSL 1.1](./LICENSE). Changes to MIT on 2030-05-15.
