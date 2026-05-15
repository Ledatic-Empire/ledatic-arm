# ledatic-arm

A Rail-native robotic arm stack — simulator, kinematics, motion control,
wire protocol, and per-action attestation. Same code drives the paper
sim and the physical arm.

## Status

Scaffold only. Waiting on the **LewanSoul MaxArm** (ESP32-based,
WiFi/Bluetooth, suction end-effector) to arrive. This repo is being
built sim-first so delivery day is a 30-minute integration: unbox ->
measure -> update `arms/maxarm.json` -> smoke-test -> first attested
move.

The single arm in scope is the MaxArm. Prior experiments around other
educational arms are out of scope.

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
| `/state` | current pulses + joint degrees + FK + chain head |
| `/pose?p1=&p2=&p3=&p4_us=` | set joint pulses (attested) |
| `/reach?x=&y=&z=` (mm) | IK + clamp + apply (attested) |
| `/nozzle?deg=` | set nozzle wrist angle (attested) |
| `/suction?cmd=on\|vent\|off` | control pump + valve (attested) |
| `/home` | reset to home (attested) |
| `/poses` | list named poses |
| `/poses/save\|load\|delete?name=` | manage poses |
| `/chain` | append-only attestation chain (every mutating action) |
| `/anchor` | beacon-anchor the chain head via fleet0 witness (requires Pi online) |
| `/anchors` | list of historical anchors |

## Roadmap

- [x] `arms/maxarm.json` — geometry/limits firmware-canonical from Hiwonder source
- [x] `src/armsim.rail` — FK/IK byte-ported from `_espmax.cpp`, HTTP handler, state, named poses
- [x] `web/index.html` — 3D viewer matching MaxArm geometry
- [x] `protocols/lewansoul.rail` — AA 55 byte protocol, 9/9 self-tests PASS
- [x] Attestation chain — every mutating action SHA-chained at `/chain`
- [x] On-demand beacon anchor — `/anchor` invokes the existing fleet0 witness pipeline
- [x] `docs/DELIVERY_DAY.md` — 30-minute integration runbook
- [ ] Real-arm bridge — `ARMSIM_AA55_HOST` env wires `/pose` into the byte protocol
- [ ] Viewer chain UI — render the chain head and recent entries in the right panel
- [ ] Behavior-cloning policy net on top of Rail GPU kernels

## License

[BSL 1.1](./LICENSE). Changes to MIT on 2030-05-15.
