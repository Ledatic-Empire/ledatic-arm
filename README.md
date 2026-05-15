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

## Roadmap

- [ ] `arms/maxarm.json` — geometry/limits from LewanSoul specs; every value tagged "calibrate on delivery"
- [ ] `src/armsim.rail` — config-driven FK/IK, HTTP handler, state, named poses
- [ ] `web/index.html` — 3D viewer matching MaxArm geometry
- [ ] `protocols/lewansoul.rail` — ESP32 wire protocol (reverse from their Python SDK)
- [ ] Attestation wrapper — sign every `(state, action)` via fleet0 witness
- [ ] `docs/DELIVERY_DAY.md` — unbox -> measure -> calibrate -> smoke-test runbook
- [ ] Behavior-cloning policy net on top of Rail GPU kernels

## License

[BSL 1.1](./LICENSE). Changes to MIT on 2030-05-15.
