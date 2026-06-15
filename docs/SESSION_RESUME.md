# ledatic-arm — RESUME POINT (2026-06-15)

> `/railarm` loads this FIRST. It's the "pick up exactly here" snapshot. When this
> drifts from reality, trust `./railarm status` + `git log` over this file and update it.

## Where we are right now
- **Arm: physically DISCONNECTED** (device absent — powered off / unplugged). Server is
  up on :7071 in **sim/off mode** (no relay, nothing drives metal). Keepalive
  `com.ledatic.armbridge` loaded. To go live: power on (12V barrel + USB), `./railarm up`.
- **Arm repo: clean, 0 unpushed**, HEAD `353ad49` (self-collision checks).
- **Compiler fix is on master:** zemo-g/rail `origin/master` = `7b2747e` (PR #12, B1+B2).

## What shipped this session (all autonomous work is DONE + pushed)
A multi-agent "tend to everything" remediation of the machine-control review, then "keep building":
- **Arm review A1–A11** (all sim-validated, pushed `b220eb8..353ad49`): observed-outcome
  attestation into the hash-chain (A1); relax-sag third ghost category so idle droop ≠ fault (A2);
  real ~120mm table-floor gate (A3); enforced `/validate_program` pre-flight + fail-closed runner (A4);
  fail-closed clamp loader — `safe_limits.json` is the only envelope source (A5); bounded-latency
  e-stop **+ fail-closed-at-startup** (A6); honest `/actual` — reach from real pulses, wrist/suction
  labeled commanded/open-loop (A7); geometry single-source accessors `g_l0..` (A8); chain
  atomicity + `verify_chain.py --repair` recovery (A9); FK `envelope_check` + `tools/envelope_map.py`
  (A10); docs + memory (A11).
- **Self-collision (config-space):** `envelope_check` reasons 5 (distal link below floor) + 6 (folded
  into base column). **Defensive-only** — a full reachability sweep proved they're unreachable in the
  safe joint ranges (the tip gate already implies distal safety); kept to complete the model +
  future-proof a range-widening. False-positives nothing (all programs + named poses validate clean).
- **Compiler B1+B2 → origin/master** (PR #12, `7b2747e`): `char_from_int(0)` → 1-byte NUL string;
  `is_float` `__float_ret_` fix for top-level float consts. **177/177 tests + byte-identical fixed point.**

## What's LEFT — needs YOU + the physical arm (the whole reason to resume)
1. **Watched hardware dance** — confirm on metal what's only sim-validated: the e-stop preemption
   and the observed-attestation. Plug in + 12V on → `ls /dev/cu.usbserial-*` → `./railarm up` →
   `./railarm open` → run a program (dance/wave/scan/nod, all re-timed to max-speed). Hand on the
   e-stop. First move proves a *commanded delta* (don't trust an absolute read).
2. **Supervised swept-volume envelope capture** — the ONE self-collision class the FK checker can't
   model (link-to-link). Tool is shipped DRY-RUN-safe: `python3 tools/envelope_map.py` (add
   `--apply`/`--live` to actually move). The session = jog combined poses slowly, you say STOP at each
   contact, it records the real limits. Do this watched, hand near power.
3. **(Optional) Badge → 177** — `ledatic.org/attest/badge` shows `170` because `daily.sh` attests the
   WORKING CLONE `~/projects/rail` (your active `feat/p3-v0-map-fwd`, 170 tests), NOT master. To show
   177: rebase your active P3 work onto master's B1+B2 + rebuild. YOUR call (touches live P3 work) — I
   left it untouched.

## Gotchas still in force (full set in REAL_ARM_OPERATION.md)
- **12V rail is 3-state:** ~12000mV = good · False/0 = off · **~3.9V = half-seated barrel jack**
  (servos talk + report position but can't move). Truth test = commanded `get_position` DELTA.
- Joints driven by **`arm.bus_servo.run`**, NOT `arm.set_servo` (the latter is broken for the base on
  this unit). Frames go as **hex** over the FIFO (`char_from_int(0)` drops NUL).
- **E-stop is fail-closed at startup** now: a latch present when the relay starts halts immediately.
- **Auto-relax sag is benign** — the ghost labels it "relaxed/sagging", not a fault.

## Authoritative refs
- `docs/REAL_ARM_OPERATION.md` (operation + §8 closed-vs-deferred + attestation honesty boundary).
- Memory `ledatic-arm-review-remediation-2026-06-15` (the full remediation + compiler-ship lessons).
- Memory `maxarm-set-servo-base-bug` (the base bug + the sim↔real loop).
