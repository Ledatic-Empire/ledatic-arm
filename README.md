# ledatic-arm

A Rail-native robotic arm stack. Simulator, kinematics, motion control,
wire protocols, and (soon) per-action attestation. Same code drives a
paper sim and a physical arm.

## Status

**Sim-first.** Two arms in scope:

- **EEZYbotARM MK2** — 3-DOF parallelogram arm. Existing sim runs today.
- **LewanSoul MaxArm** — ~4-5 DOF ESP32-based arm with suction
  end-effector. Arriving soon. This repo's job is to make delivery day
  a 30-minute integration: unbox -> measure -> update `arms/maxarm.json`
  -> smoke test -> first attested move.

## Layout

```
arms/        per-arm config (geometry, joint limits, servo channels)
src/         Rail source (FK/IK, trajectory, HTTP server)
protocols/   wire protocols (ESP32 HTTP, USB serial)
web/         browser viewer (plain HTML/CSS/JS)
docs/        phase docs, calibration notes, delivery runbook
serve.py     thin TCP loop; per request, execs the compiled Rail handler
run.sh       compile + serve
```

## Quickstart

Requires a Rail compiler (`github.com/zemo-g/rail`).

```bash
git clone https://github.com/zemo-g/rail ~/projects/rail
# follow rail's README to produce a rail_native binary

git clone https://github.com/Ledatic-Empire/ledatic-arm ~/projects/ledatic-arm
cd ~/projects/ledatic-arm
./run.sh 7071
open http://localhost:7071/
```

The browser renders an EEZYbotARM MK2 and lets you drive it via joint
commands, IK targets, or named poses.

## Why Rail

Every kernel the GPU runs and every byte the network sends is
Rail-emitted, replayable, and signed against the public entropy beacon
at <https://ledatic.org/entropy>. The arm makes that thesis physical:
every commanded pose can carry an attestation tuple
`(state, action, model_hash, kernel_hash, beacon_pulse)`. Auditable
motion.

## Roadmap

- [x] Sim Phase A — EEZYbotARM, browser viewer, IK, named poses
- [ ] Config-driven FK/IK (sim drives any arm via `arms/<name>.json`)
- [ ] `arms/maxarm.json` — best-guess from LewanSoul specs; calibrate on delivery
- [ ] `protocols/lewansoul.rail` — ESP32 HTTP wire protocol
- [ ] Attestation wrapper — sign every `(state, action)` via fleet0 witness
- [ ] `docs/DELIVERY_DAY.md` — measurement + calibration + smoke-test runbook
- [ ] Behavior-cloning policy net on top of Rail GPU kernels
- [ ] Phase B — physical EEZYbotARM (`docs/PHASE_B_HARDWARE.md`)

## License

[BSL 1.1](./LICENSE). Changes to MIT on 2030-05-15.
