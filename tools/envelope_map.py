#!/usr/bin/env python3
# envelope_map.py -- SUPERVISED swept-volume (combined-pose) envelope mapper
# for the MaxArm (A10 / ticket cluster safety-gates).
#
# WHY THIS EXISTS
# ---------------
# arms/safe_limits.json carries PER-SERVO ranges (each joint jogged from home
# in isolation, tools/arm_limits.py). Those bounds do NOT capture COMBINED-pose
# hazards: a shoulder+elbow pair that is individually in-range can still drive
# the tip into the table or fold the arm onto its own forearm. The autonomous
# guard for that is src/armsim.rail `envelope_check` (floor + base-column +
# max-reach heuristics) -- honest but incomplete: it does NOT model link-to-link
# self-intersection. That last gap genuinely requires MEASURING the physical
# swept volume with a human watching. This tool does that measurement.
#
# It nested-sweeps (shoulder, elbow) on a coarse grid, jogs SLOWLY to each
# combined pose, then STOPS and waits for an operator keypress. The operator --
# eyes on the arm, hand near the e-stop / power -- classifies each pose:
#     ENTER : safe (clears the pose, advance)
#     s     : STOP-contact at this pose (records last-safe elbow for this
#             shoulder bin; skip the rest of this shoulder's elbow sweep)
#     b     : back off one step and re-home (pose felt unsafe, no contact yet)
#     q     : abort the whole sweep, go_home, write partial results
# It accumulates a per-shoulder-bin "max safe elbow" table and prints
# RESULTS=<json> for a human to fold into safe_limits.json as a refined
# combined envelope. It does NOT write safe_limits.json itself.
#
# SAFETY MODEL (this tool is part of a SAFETY system -- fail-closed):
#   * DRY-RUN BY DEFAULT. With no flag it NEVER opens the serial port and
#     NEVER moves the arm. It prints the grid it WOULD jog and exits 0.
#     The physical run is DEFERRED to a scheduled human session.
#   * Moving the arm requires an EXPLICIT --apply (alias --live) flag.
#   * get_position senses servo STALLS, not soft table/self contact, so it
#     cannot self-certify a pose -- a HUMAN must classify every pose. The tool
#     refuses to auto-advance past an unconfirmed pose.
#   * Requires the bus servos under MAIN POWER (USB alone will not hold the
#     loom against gravity). go_home on every exit path (clean, abort, error).
#   * Uses arm.bus_servo.run(id,pulse,dur) -- NOT arm.set_servo (broken per
#     docs/REAL_ARM_OPERATION.md).
#
# Modeled on tools/arm_limits.py (serial + MicroPython REPL @115200) and the
# repl_relay.py command vocabulary. Reads the grid bounds from
# arms/safe_limits.json so the sweep can never command past the per-servo caps.
#
# USAGE
#   tools/envelope_map.py                 # DRY-RUN: print grid, no serial, no motion
#   tools/envelope_map.py --list          # same as dry-run, explicit
#   tools/envelope_map.py --apply         # LIVE: opens serial, MOVES the arm
#                                         #   (requires a watching human + main power)
#   tools/envelope_map.py --apply --dev /dev/cu.usbserial-XXX --dur 900 --step 30
#
# Options:
#   --apply / --live   actually open the port and move (default: dry-run)
#   --dev PATH         serial device (default: auto-detect /dev/cu.usbserial-*)
#   --baud N           baud (default 115200)
#   --dur MS           per-move duration ms, SLOW (default 900)
#   --step N           elbow pulse step within each shoulder bin (default 30)
#   --shoulder-step N  shoulder pulse step between bins (default 40)
#   --out PATH         also write RESULTS json to this file (default: stdout only)

import sys, os, json, glob, argparse, time
from typing import NoReturn

HERE = os.path.dirname(os.path.abspath(__file__))
LIMITS_PATH = os.path.join(HERE, "..", "arms", "safe_limits.json")
HOME = 500


def die(msg, code=2) -> NoReturn:
    sys.stderr.write("envelope_map: " + msg + "\n")
    sys.exit(code)


# ── Load the per-servo caps (fail-closed: this tool must never command past
#    the measured per-servo safe ranges; the grid is clamped to them). ──────
def load_caps():
    if not os.path.exists(LIMITS_PATH):
        die("safe_limits.json missing at %s" % LIMITS_PATH)
    try:
        with open(LIMITS_PATH) as fh:
            j = json.load(fh)
    except Exception as e:
        die("safe_limits.json unreadable (%s)" % e)
    sp = j.get("servo_pulse")
    if not isinstance(sp, dict):
        die("safe_limits.json missing/invalid servo_pulse")
    caps = {}
    for k in ("1", "2", "3"):
        if k not in sp:
            die("safe_limits.json servo_pulse missing servo '%s'" % k)
        pair = sp[k]
        if not (isinstance(pair, list) and len(pair) == 2):
            die("safe_limits.json servo_pulse[%s] not a [lo,hi] pair" % k)
        lo, hi = pair
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)):
            die("safe_limits.json servo_pulse[%s] non-numeric bound" % k)
        if lo >= hi:
            die("safe_limits.json servo_pulse[%s] lo>=hi" % k)
        caps[int(k)] = (int(lo), int(hi))
    return caps


def build_grid(caps, shoulder_step, elbow_step):
    """Nested (shoulder, [elbow...]) grid, clamped to the per-servo caps.
    Shoulder bins low->high; within each, elbow swept low->high. The HOME
    column (shoulder=500) is visited first as a sanity anchor."""
    s_lo, s_hi = caps[2]
    e_lo, e_hi = caps[3]
    shoulders = list(range(s_lo, s_hi + 1, shoulder_step))
    if s_hi not in shoulders:
        shoulders.append(s_hi)
    # Visit the home shoulder bin first if it is within the cap.
    if s_lo <= HOME <= s_hi and HOME not in shoulders:
        shoulders = [HOME] + shoulders
    elif HOME in shoulders:
        shoulders.remove(HOME)
        shoulders = [HOME] + shoulders
    grid = []
    for sp in shoulders:
        elbows = list(range(e_lo, e_hi + 1, elbow_step))
        if e_hi not in elbows:
            elbows.append(e_hi)
        grid.append((sp, elbows))
    return grid


def autodetect_dev():
    cands = sorted(glob.glob("/dev/cu.usbserial-*")) + sorted(glob.glob("/dev/tty.usbserial-*"))
    return cands[0] if cands else None


def print_grid(grid, args, caps, dev_label):
    total = sum(len(es) for _, es in grid)
    print("=== envelope_map DRY-RUN (no serial, no motion) ===")
    print("safe_limits servo_pulse caps: %s" % caps)
    print("serial device (would use): %s" % dev_label)
    print("per-move duration: %d ms   elbow step: %d   shoulder step: %d"
          % (args.dur, args.step, args.shoulder_step))
    print("base p1 held at home=%d, wrist/suction untouched" % HOME)
    print("combined poses to visit (p1=%d fixed):" % HOME)
    for sp, elbows in grid:
        print("  shoulder p2=%d  ->  elbow p3 in %s" % (sp, elbows))
    print("TOTAL combined poses: %d" % total)
    print("")
    print("This is a SUPERVISED tool. To run for real (MOVES THE ARM):")
    print("  ensure MAIN POWER is on and a human is watching the arm + e-stop, then:")
    print("  %s --apply" % os.path.basename(sys.argv[0]))


# ── LIVE serial path (only reached with --apply). Mirrors arm_limits.py. ────
def run_live(grid, args, caps):
    try:
        import serial  # noqa: import here so dry-run needs no pyserial
    except Exception:
        die("pyserial not installed; needed for --apply (pip install pyserial)")

    dev = args.dev or autodetect_dev()
    if not dev:
        die("no serial device found (set --dev /dev/cu.usbserial-XXX)")
    if not os.path.exists(dev):
        die("serial device %s does not exist" % dev)

    print("=== envelope_map LIVE -- THE ARM WILL MOVE ===")
    print("device=%s baud=%d  WATCH THE ARM. Hand near power/e-stop." % (dev, args.baud))
    print("Per pose:  ENTER=safe  s=STOP-contact  b=back-off+rehome  q=abort")
    try:
        ack = input("Main power on and a human watching? type 'yes' to proceed: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\naborted before opening serial.")
        return 1
    if ack != "yes":
        print("not confirmed; refusing to move. exiting.")
        return 1

    ser = serial.Serial(dev, args.baud, timeout=0.4)
    time.sleep(2.5)
    ser.reset_input_buffer()
    ser.write(b"\r\n")
    ser.flush()
    time.sleep(0.3)
    ser.reset_input_buffer()

    def cmd(line, rs=2.0):
        ser.reset_input_buffer()
        ser.write((line + "\r\n").encode())
        ser.flush()
        t0 = time.time()
        buf = b""
        while time.time() - t0 < rs:
            b = ser.read(256)
            if b:
                buf += b
            if buf.endswith(b">>> "):
                break
        return buf.decode("utf-8", "replace")

    def go_home():
        cmd("arm.go_home()", rs=2.5)
        time.sleep(1.0)

    def runto(p2, p3, dur):
        # base held at home; combined shoulder+elbow move, SLOW.
        cmd("arm.bus_servo.run(1,%d,%d)" % (HOME, dur))
        cmd("arm.bus_servo.run(2,%d,%d)" % (p2, dur))
        cmd("arm.bus_servo.run(3,%d,%d)" % (p3, dur))
        # wait out the move plus a settle margin before handing to the human.
        time.sleep(dur / 1000.0 + 0.6)

    results = {}        # shoulder p2 -> {"max_safe_elbow":p3 or None, "why":...}
    rc = 0
    try:
        # load the three bus servos so they hold against gravity.
        cmd("arm.bus_servo.load(1);arm.bus_servo.load(2);arm.bus_servo.load(3)")
        time.sleep(0.5)
        go_home()
        for sp, elbows in grid:
            print("\n--- shoulder p2=%d ---" % sp)
            last_safe = None
            stopped = False
            for ep in elbows:
                runto(sp, ep, args.dur)
                try:
                    key = input("  p2=%d p3=%d  [ENTER=safe s=stop b=backoff q=abort]: "
                                % (sp, ep)).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    key = "q"
                if key == "q":
                    print("  abort requested.")
                    results[sp] = {"max_safe_elbow": last_safe, "why": "aborted"}
                    rc = 0
                    raise StopIteration
                elif key == "s":
                    print("  STOP-contact recorded at p3=%d; last safe=%s" % (ep, last_safe))
                    results[sp] = {"max_safe_elbow": last_safe, "why": "stop@%d" % ep}
                    stopped = True
                    break
                elif key == "b":
                    print("  back off + rehome (pose felt unsafe, no contact).")
                    results[sp] = {"max_safe_elbow": last_safe, "why": "backoff@%d" % ep}
                    go_home()
                    stopped = True
                    break
                else:
                    last_safe = ep
            if not stopped:
                results[sp] = {"max_safe_elbow": last_safe, "why": "swept_full"}
            go_home()
    except StopIteration:
        pass
    except Exception as e:
        sys.stderr.write("envelope_map: LIVE error (%s) -- homing.\n" % e)
        rc = 3
    finally:
        try:
            go_home()
            cmd("arm.teaching_mode()")   # relax servos so they don't hold torque
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass

    payload = {
        "tool": "envelope_map",
        "device": dev,
        "caps": {str(k): list(v) for k, v in caps.items()},
        "base_p1": HOME,
        "dur_ms": args.dur,
        "results": {str(k): v for k, v in results.items()},
        "_note": "operator-confirmed combined (shoulder,elbow) safe set. Fold "
                 "into arms/safe_limits.json as a refined combined envelope; "
                 "do NOT auto-apply.",
    }
    out = "RESULTS=" + json.dumps(payload)
    print("\n" + out)
    if args.out:
        try:
            with open(args.out, "w") as fh:
                fh.write(out + "\n")
            print("wrote %s" % args.out)
        except Exception as e:
            sys.stderr.write("envelope_map: could not write --out %s (%s)\n" % (args.out, e))
    return rc


def main():
    ap = argparse.ArgumentParser(
        description="Supervised swept-volume envelope mapper for the MaxArm "
                    "(DRY-RUN by default; --apply to move the arm).")
    ap.add_argument("--apply", "--live", dest="apply", action="store_true",
                    help="actually open the serial port and MOVE the arm "
                         "(requires a watching human + main power). Default: dry-run.")
    ap.add_argument("--list", dest="list_only", action="store_true",
                    help="print the grid that would be jogged and exit (same as dry-run).")
    ap.add_argument("--dev", default=None, help="serial device (auto-detect if omitted)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dur", type=int, default=900, help="per-move ms (SLOW)")
    ap.add_argument("--step", type=int, default=30, help="elbow pulse step")
    ap.add_argument("--shoulder-step", type=int, default=40, help="shoulder pulse step")
    ap.add_argument("--out", default=None, help="write RESULTS json to this path")
    args = ap.parse_args()

    caps = load_caps()
    grid = build_grid(caps, args.shoulder_step, args.step)

    if not args.apply or args.list_only:
        dev_label = args.dev or (autodetect_dev() or "<none found>")
        print_grid(grid, args, caps, dev_label)
        return 0

    return run_live(grid, args, caps)


if __name__ == "__main__":
    sys.exit(main())
