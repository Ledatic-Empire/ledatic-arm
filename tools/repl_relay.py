#!/usr/bin/env python3
"""repl_relay.py -- translate the sim's AA-55 bridge frames into MicroPython
REPL commands for a factory-firmware MaxArm, with safety clamping and
auto-relax for safe long-term / unattended running.

The ledatic-arm sim (armsim.rail) writes HEX-encoded AA-55 frames (one per line)
to the bridge FIFO. The physical MaxArm runs factory MicroPython-REPL firmware
@115200 (see memory maxarm-firmware-reconciliation-2026-06-07), which wants
Python text. This relay sits between them: parse hex frame -> clamp -> translate
-> send over the serial port (held open @115200, reopening the FIFO on EOF).
Hex, not raw binary, because Rail's char_from_int(0) yields "" -> raw binary
drops every 0x00 byte (any pulse < 256 corrupts).

Translations:
  0x01 SET_ANGLE  p1,p2,p3,t  -> arm.bus_servo.run(1..3,p,t) (loads servos if relaxed)
  0x03 SET_XYZ    x,y,z,t     -> arm.set_position((x,y,z),t)
  0x05 SET_PWMSERVO us,t      -> nozzle.set_angle(deg,t)     (wrist, us->deg clamped)
  0x07 SET_SUCTION cmd        -> nozzle.on() / nozzle.off()  (vacuum pump suck/vent)
  0x11/0x13 READ_*           -> ignored (one-way bridge)

AUTO-RELAX (the long-term-safety mechanism): the 3 big HTS-35H bus servos
overheat if they HOLD a loaded pose. A watchdog thread unloads them
(arm.teaching_mode()) after IDLE_RELAX_SEC with no joint move; the next move
re-loads them (arm.bus_servo.load). So the arm holds torque only while actively
moving / briefly after, never long enough to cook. Configure via
arms/safe_limits.json "idle_relax_sec" (0 disables). The small PWM wrist + pump
are not bus servos and are left as commanded.

SAFETY (fail-closed): arms/safe_limits.json is the SINGLE source of the clamp
envelope. If it is missing/malformed/inverted the relay REFUSES TO START
(sys.exit(2)) — there is no hardcoded fallback. The out-of-band e-stop latch
/tmp/armsim_estop.txt (written by armsim.rail route_estop, deleted by route_clear)
is watched @50ms: absent->present halts (stop 1..3, drops the FIFO move backlog);
present->absent resumes. The relay only reads that file, never creates/deletes it.

Usage:  repl_relay.py <serial_dev> <baud=115200> <fifo> [attest_url]
        attest_url defaults to $ARMSIM_ATTEST_URL or http://127.0.0.1:7071
"""
import os, re, sys, time, json, threading, serial
import urllib.request, urllib.parse
from typing import NoReturn

DEV  = sys.argv[1]
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
FIFO = sys.argv[3]
# A1 (C4): where to POST observed readbacks. argv[4] > env > default.
ARMSIM_ATTEST_URL = (sys.argv[4] if len(sys.argv) > 4
                     else os.environ.get("ARMSIM_ATTEST_URL", "http://127.0.0.1:7071")).rstrip("/")

# ── real-pose readback (feeds the viewer's /actual ghost arm) ──
# The relay is the only process holding the serial port, so it is the only
# thing that can read the arm back. A poll thread publishes the live servo
# pulses here; armsim.rail's /actual route (fifo mode) reads this file and
# runs the SAME FK as /state, so the ghost reflects the arm's true pose.
REAL_PATH = "/tmp/armsim_real.txt"
REAL_TMP  = "/tmp/armsim_real.tmp"
POLL_SEC  = 0.5                       # matches the viewer's ACTUAL_POLL_MS
_INT3 = re.compile(r"^(-?\d+)\s+(-?\d+)\s+(-?\d+)$")

def log(m):
    sys.stderr.write(m + "\n"); sys.stderr.flush()

# ── Safe ranges + relax policy: arms/safe_limits.json is the SINGLE SOURCE OF
#    TRUTH (A5 / C11). There is NO hardcoded fallback envelope. If the file is
#    missing/malformed/inverted the relay REFUSES TO START (sys.exit(2)) — a
#    relay that cannot load its clamp envelope has no safe behaviour except to
#    not run. Do NOT reintroduce default ranges, and do NOT wrap this relay in an
#    auto-restart loop (that would re-bury the fail-closed signal; see
#    enable_bridge.sh start_repl_relay). ──
_limits = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arms", "safe_limits.json")

def _die_limits(msg) -> NoReturn:
    log("FATAL: %s -- refusing to start (fail-closed)" % msg)
    sys.exit(2)

def _range_or_die(name, v):
    # v must be a 2-element [lo, hi] of finite real numbers with lo < hi.
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        _die_limits("safe_limits.json key %s is not a [lo,hi] pair" % name)
    lo, hi = v
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        _die_limits("safe_limits.json key %s has non-numeric bound(s)" % name)
    if isinstance(lo, bool) or isinstance(hi, bool):
        _die_limits("safe_limits.json key %s has boolean bound(s)" % name)
    if lo != lo or hi != hi or lo in (float("inf"), float("-inf")) or hi in (float("inf"), float("-inf")):
        _die_limits("safe_limits.json key %s has non-finite bound(s)" % name)
    if not (lo < hi):
        _die_limits("safe_limits.json key %s has lo>=hi (%s>=%s) -- inverted/degenerate range" % (name, lo, hi))
    return (lo, hi)

def load_limits_or_die():
    """Strict, fail-closed loader. Returns (SAFE_PULSE, SAFE_WRIST_US, SAFE_XYZ,
    IDLE_RELAX_SEC) or exits 2. Never opens the serial port / starts threads
    with an unknown envelope (call this BEFORE serial.Serial)."""
    if not os.path.exists(_limits):
        _die_limits("safe_limits.json missing at %s" % _limits)
    try:
        with open(_limits) as fh:
            j = json.load(fh)
    except (ValueError, OSError) as e:
        _die_limits("safe_limits.json unreadable (%s)" % e)
    if not isinstance(j, dict):
        _die_limits("safe_limits.json is not a JSON object")
    # --- servo_pulse: MANDATORY, keys '1','2','3' each a [lo,hi] ---
    sp = j.get("servo_pulse")
    if not isinstance(sp, dict):
        _die_limits("safe_limits.json missing/invalid key servo_pulse")
    safe_pulse = {}
    for k in ("1", "2", "3"):
        if k not in sp:
            _die_limits("safe_limits.json servo_pulse missing servo '%s'" % k)
        lo, hi = _range_or_die("servo_pulse[%s]" % k, sp[k])
        if lo <= 0:
            _die_limits("safe_limits.json servo_pulse[%s] lo<=0 (%s) -- pulse must be >0" % (k, lo))
        safe_pulse[int(k)] = (int(lo), int(hi))
    # --- wrist_us: MANDATORY [lo,hi] ---
    if "wrist_us" not in j:
        _die_limits("safe_limits.json missing key wrist_us")
    wlo, whi = _range_or_die("wrist_us", j["wrist_us"])
    if wlo <= 0:
        _die_limits("safe_limits.json wrist_us lo<=0 (%s) -- pulse must be >0" % wlo)
    safe_wrist_us = (int(wlo), int(whi))
    # --- xyz: MANDATORY dict with x/y/z each [lo,hi] ---
    xyz = j.get("xyz")
    if not isinstance(xyz, dict):
        _die_limits("safe_limits.json missing/invalid key xyz")
    safe_xyz = {}
    for a in ("x", "y", "z"):
        if a not in xyz:
            _die_limits("safe_limits.json xyz missing axis '%s'" % a)
        lo, hi = _range_or_die("xyz[%s]" % a, xyz[a])
        safe_xyz[a] = (int(lo), int(hi))
    # --- idle_relax_sec: ONLY optional field (relax policy is a config choice,
    #     not a safety envelope). Default 10 explicitly, log the defaulting. ---
    if "idle_relax_sec" in j:
        irs = j["idle_relax_sec"]
        if isinstance(irs, bool) or not isinstance(irs, (int, float)):
            _die_limits("safe_limits.json idle_relax_sec is non-numeric")
        idle_relax_sec = irs
    else:
        idle_relax_sec = 10
        log("safe_limits.json has no idle_relax_sec -- defaulting to 10s")
    log("safe limits loaded from %s" % _limits)
    return safe_pulse, safe_wrist_us, safe_xyz, idle_relax_sec

SAFE_PULSE, SAFE_WRIST_US, SAFE_XYZ, IDLE_RELAX_SEC = load_limits_or_die()
log("SAFE pulse=%s  wrist_us=%s  xyz=%s  idle_relax=%ss" % (SAFE_PULSE, SAFE_WRIST_US, SAFE_XYZ, IDLE_RELAX_SEC))

# Sentinel returned by clamp_* for an UNKNOWN servo/axis: any envelope the relay
# cannot positively validate must result in NO motion, never a fabricated range.
DROP_FRAME = object()

def clamp_pulse(s, p):
    rng = SAFE_PULSE.get(s)
    if rng is None:                       # unknown servo index -> fail CLOSED (drop)
        log("  CLAMP servo%d UNKNOWN -> frame dropped (no validated envelope)" % s)
        return DROP_FRAME
    lo, hi = rng; c = max(lo, min(hi, p))
    if c != p: log("  CLAMP servo%d %d -> %d (safe %d..%d)" % (s, p, c, lo, hi))
    return c
def clamp_axis(a, v):
    rng = SAFE_XYZ.get(a)
    if rng is None:                       # unknown axis -> fail CLOSED (drop)
        log("  CLAMP %s UNKNOWN -> frame dropped (no validated envelope)" % a)
        return DROP_FRAME
    lo, hi = rng; c = max(lo, min(hi, v))
    if c != v: log("  CLAMP %s %d -> %d (safe %d..%d)" % (a, v, c, lo, hi))
    return c
def clamp_us(us):
    lo, hi = SAFE_WRIST_US; c = max(lo, min(hi, us))
    if c != us: log("  CLAMP wrist_us %d -> %d (safe %d..%d)" % (us, c, lo, hi))
    return c

ser = serial.Serial(DEV, BAUD, timeout=0.5)
time.sleep(2.5)                      # ESP32 reboots when the port opens
ser.reset_input_buffer()
ser.write(b"\r\n"); ser.flush(); time.sleep(0.3); ser.reset_input_buffer()
log("repl-relay: %s @%d, FIFO=%s (AA-55 -> REPL)" % (DEV, BAUD, FIFO))
try: os.remove(REAL_PATH)             # clear any stale ghost from a prior run
except OSError: pass

def le16(lo, hi):
    v = lo | (hi << 8)
    return v - 65536 if v >= 32768 else v

# ── one lock serializes ALL REPL writes (main thread + relax watchdog + poll) ──
# INVARIANT (A6 / C12): never hold _lock across more than ONE _send except
# joint_move's load+move pair. The e-stop's worst-case wait on _lock is the tail
# of at most one in-flight _send; growing the hold breaks the latency bound.
_lock = threading.Lock()
_loaded = True                       # firmware boots with the bus servos loaded
_last_move = time.time()

# ── e-stop (A6 / C12) ──
# /tmp/armsim_estop.txt is the OUT-OF-BAND e-stop signal. armsim.rail route_estop
# WRITES "1"; route_clear DELETES it. The relay ONLY reads/stats it (@50ms) and
# acts on the edges: absent->present sets _estop + sends stop; present->absent
# clears _estop. The relay NEVER creates or deletes this file.
ESTOP_PATH = "/tmp/armsim_estop.txt"
ESTOP_POLL_SEC = 0.05                # 50ms — out-of-band, skips the FIFO backlog
_estop = threading.Event()

# ── command-seq tracking for observed-attestation (A1 / C4) ──
# A monotonic seq bumped on every pose-mutating command (SET_ANGLE / SET_XYZ).
# After the arm settles, pose_poll fires ONE /attest_observed GET for the pending
# seq (idempotency is server-side by seq). Wrist/suction do NOT bump it (open-loop).
OBSERVE_EPS  = 4                     # pulses: |r_n - r_{n-1}| <= this on all 3 joints = settled
SETTLE_GRACE_S = 0.6                 # min seconds since last move before observing
_cmd_seq = 0                         # last commanded seq (0 = none yet)
_observed_seq = 0                    # last seq we fired an observed trigger for
_seq_lock = threading.Lock()         # guards _cmd_seq / _observed_seq (cheap, never held across I/O)

def _send(line):                     # caller MUST hold _lock
    ser.reset_input_buffer()
    ser.write((line + "\r\n").encode()); ser.flush()
    t0 = time.time(); buf = b""
    while time.time() - t0 < 0.4:
        b = ser.read(128)
        if b: buf += b
        if buf.endswith(b">>> "): break
    return buf

def joint_move(line):                # bus-servo move: re-load if relaxed, then move
    global _loaded, _last_move, _cmd_seq
    with _lock:                      # the ONLY place two _sends are held under one lock (load+move)
        if not _loaded:
            _send("arm.bus_servo.load(1);arm.bus_servo.load(2);arm.bus_servo.load(3)")
            _loaded = True; log("  LOAD (re-engage from relaxed)")
        _send(line); _last_move = time.time()
    with _seq_lock:                  # A1: mark a new pose-mutating command for observation
        _cmd_seq += 1

def aux_send(line):                  # wrist / suction: no load, but keep the arm awake
    global _last_move
    with _lock:
        _send(line); _last_move = time.time()

def _publish_real(p1, p2, p3, loaded):   # atomic write so the rail handler never reads a torn file
    # C1/C2: format is "p1 p2 p3 L" — 4 space-separated tokens, NO trailing
    # newline (clean parse_int), L = loaded flag (1=loaded, 0=relaxed/sagging).
    # armsim.rail route_actual_fifo reads this; a reader seeing <4 tokens must
    # default L=1 (never relaxed).
    try:
        with open(REAL_TMP, "w") as fh:
            fh.write("%d %d %d %d" % (p1, p2, p3, 1 if loaded else 0))
        os.replace(REAL_TMP, REAL_PATH)
    except Exception as e:
        log("  real-publish err: %s" % e)

def fire_observed(seq, r1, r2, r3, loaded):
    # A1 / C4: best-effort GET <ARMSIM_ATTEST_URL>/attest_observed?seq&op1..3&loaded.
    # MUST be called OUTSIDE _lock (never delay an e-stop), short timeout,
    # try/except — a failed/blocked trigger must never crash the relay or block
    # motion. Idempotency is server-side by seq, so a retry is harmless.
    qs = urllib.parse.urlencode({"seq": seq, "op1": r1, "op2": r2, "op3": r3,
                                 "loaded": 1 if loaded else 0})
    url = "%s/attest_observed?%s" % (ARMSIM_ATTEST_URL, qs)
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            resp.read(512)           # drain a small body; ignore content
    except Exception as e:
        log("  attest-observed trigger failed (%s) -- non-fatal" % e)

def pose_poll():
    # Read the live servo positions and publish them for /actual (the ghost arm).
    # READ-ONLY: never touches _last_move, so the relax watchdog still fires; when
    # the arm is relaxed, get_position reports the SAGGED pose, so the ghost shows
    # the real droop. One combined query = one REPL round trip; the lock is free
    # during the POLL_SEC sleep so user moves take priority.
    global _observed_seq
    if POLL_SEC <= 0: return
    time.sleep(3.0)                  # let the ESP32 finish booting before first query
    q = ("print(arm.bus_servo.get_position(1),"
         "arm.bus_servo.get_position(2),"
         "arm.bus_servo.get_position(3))")
    prev = None                      # last good (r1,r2,r3) for settle detection
    while True:
        time.sleep(POLL_SEC)
        # A6 / C12: while an e-stop is active, do NOT contend for the serial port
        # (the halt's stop() is being delivered / the arm is held) — skip readback.
        if _estop.is_set():
            continue
        try:
            with _lock:               # hold only for the single _send; snapshot _loaded here
                buf = _send(q)
                loaded_now = _loaded  # mutated under _lock by joint_move/relax_watchdog
            r = None
            for ln in buf.decode("utf-8", "replace").replace("\r", "").split("\n"):
                ln = ln.strip()
                if not ln or "get_position" in ln or ln.startswith(">>>"):
                    continue          # skip the echoed command + prompt
                m = _INT3.match(ln)   # the only bare "<int> <int> <int>" line is the output
                if m:
                    r = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    break             # a non-int reading (e.g. False) -> skip, keep last good
            if r is None:
                continue              # no fresh readback this cycle — keep last good
            _publish_real(r[0], r[1], r[2], loaded_now)
            # ── A1 settle detection (compute outside the serial lock) ──
            # Settled when this readback matches the previous within OBSERVE_EPS
            # on all 3 joints AND enough grace has elapsed since the last move AND
            # there is an unobserved command pending. Fire ONE observed trigger.
            settled = (prev is not None
                       and abs(r[0] - prev[0]) <= OBSERVE_EPS
                       and abs(r[1] - prev[1]) <= OBSERVE_EPS
                       and abs(r[2] - prev[2]) <= OBSERVE_EPS
                       and (time.time() - _last_move) >= SETTLE_GRACE_S)
            prev = r
            if settled:
                with _seq_lock:
                    pending = _cmd_seq > _observed_seq
                    seq = _cmd_seq
                    if pending:
                        _observed_seq = _cmd_seq   # claim it before the network call (idempotent server-side)
                if pending:
                    fire_observed(seq, r[0], r[1], r[2], loaded_now)  # OUTSIDE _lock
        except Exception as e:
            log("  pose-poll err: %s" % e); time.sleep(0.5)

def relax_watchdog():
    global _loaded
    if IDLE_RELAX_SEC <= 0: return
    while True:
        time.sleep(2)
        # A6 / C12: never re-take the lock or issue teaching_mode while an e-stop
        # stop() is being delivered (avoid fighting the halt).
        if _estop.is_set():
            continue
        with _lock:
            if _loaded and (time.time() - _last_move) > IDLE_RELAX_SEC:
                _send("arm.teaching_mode()"); _loaded = False
                log("  AUTO-RELAX (idle >%ss) -> teaching_mode (servos unloaded)" % IDLE_RELAX_SEC)

def estop_watcher():
    # A6 / C12: out-of-band e-stop. Stat ESTOP_PATH every 50ms (cheap, no serial
    # I/O on the steady state). The relay only READS this file; it NEVER creates
    # or deletes it (armsim.rail route_estop writes "1", route_clear deletes).
    #   absent -> present : SET _estop FIRST (so poll/relax stand down on their
    #                       next check), THEN acquire _lock and send stop. Because
    #                       the flag is set before the lock wait, the stop only
    #                       contends with the single _send already in progress.
    #   present -> absent : clear _estop (operator released).
    # stop() is idempotent, so the redundant in-band 0x7F HALT path is harmless.
    # FAIL-CLOSED START: seed `present=False` (NOT the live file state) so that a
    # latch ALREADY present at relay startup -- crash-restart while halted with no
    # intervening /clear, or /estop tripped during the ~5.5s serial-boot window
    # before this thread launches -- is seen as an absent->present edge on the first
    # tick and immediately halts. Seeding from os.path.exists() would treat an
    # asserted latch as steady-state, never fire the edge, and let a later move
    # actuate despite an active e-stop (fail-OPEN). Always wake into the safe state.
    present = False
    while True:
        time.sleep(ESTOP_POLL_SEC)
        try:
            now = os.path.exists(ESTOP_PATH)
        except OSError:
            now = present
        if now and not present:
            _estop.set()              # signal poll/relax to stand down BEFORE the lock wait
            log("  E-STOP latch present -> halting (bus_servo.stop 1..3)")
            try:
                with _lock:
                    _send("arm.bus_servo.stop(1);arm.bus_servo.stop(2);arm.bus_servo.stop(3)")
            except Exception as e:
                log("  e-stop stop() send err: %s" % e)
        elif present and not now:
            _estop.clear()
            log("  E-STOP latch cleared -> resuming")
        present = now

threading.Thread(target=relax_watchdog, daemon=True).start()
threading.Thread(target=pose_poll, daemon=True).start()
threading.Thread(target=estop_watcher, daemon=True).start()

def handle(func, data):
    # A6 / C12: drain the stale FIFO move backlog while halted. Moves buffered in
    # the FIFO ahead of (or behind) the halt predate the e-stop and must NOT be
    # replayed — drop them. HALT (0x7F) and CLEAR (0x7E) are still processed.
    if _estop.is_set() and func in (0x01, 0x03, 0x05, 0x07):
        log("  [estop active] dropping queued %s frame" % hex(func)); return
    if func == 0x01 and len(data) == 8:          # SET_ANGLE
        p1 = clamp_pulse(1, le16(data[0], data[1])); p2 = clamp_pulse(2, le16(data[2], data[3]))
        p3 = clamp_pulse(3, le16(data[4], data[5])); t  = le16(data[6], data[7])
        if DROP_FRAME in (p1, p2, p3):           # unknown servo / unvalidated envelope -> no motion
            log("  SET_ANGLE -> frame dropped (clamp could not validate a servo)"); return
        log("  SET_ANGLE -> p=(%d,%d,%d) t=%d" % (p1, p2, p3, t))
        # bus_servo.run, NOT set_servo: on this unit arm.set_servo(1,...) is broken
        # for the base -- it ignores the pulse and parks servo 1 at ~691 (verified
        # live 2026-06-14). run() drives all three correctly. The relay's own clamp
        # (safe_limits.json) keeps pulses inside the firmware soft-limits, so we lose
        # nothing by skipping set_servo's clamping.
        joint_move("arm.bus_servo.run(1,%d,%d);arm.bus_servo.run(2,%d,%d);arm.bus_servo.run(3,%d,%d)" % (p1, t, p2, t, p3, t))
    elif func == 0x03 and len(data) == 8:        # SET_XYZ
        x = clamp_axis("x", le16(data[0], data[1])); y = clamp_axis("y", le16(data[2], data[3]))
        z = clamp_axis("z", le16(data[4], data[5])); t = le16(data[6], data[7])
        if DROP_FRAME in (x, y, z):              # unknown axis / unvalidated envelope -> no motion
            log("  SET_XYZ -> frame dropped (clamp could not validate an axis)"); return
        log("  SET_XYZ -> (%d,%d,%d) t=%d" % (x, y, z, t))
        joint_move("arm.set_position((%d,%d,%d),%d)" % (x, y, z, t))
    elif func == 0x05 and len(data) == 4:        # SET_PWMSERVO -> wrist
        us = clamp_us(le16(data[0], data[1])); t = le16(data[2], data[3])
        ang = int(round((us - 1500) * 90.0 / 1000.0))
        log("  SET_PWMSERVO us=%d -> wrist %d deg t=%d" % (us, ang, t))
        aux_send("nozzle.set_angle(%d,%d)" % (ang, t))
    elif func == 0x07 and len(data) >= 1:        # SET_SUCTION -> vacuum pump
        sc = data[0]; c = "nozzle.on()" if sc == 0x01 else "nozzle.off()"
        log("  SET_SUCTION 0x%02x -> %s" % (sc, c)); aux_send(c)
    elif func == 0x7F:                            # HALT (e-stop) -> stop in-flight motion NOW
        # In-band redundant path to estop_watcher (C12). Set _estop FIRST so
        # poll/relax stand down before the lock wait, THEN send the stop. The
        # estop_watcher (file latch) is the primary, backlog-skipping path; this
        # frame is the in-band record and a second trigger. stop() is idempotent.
        _estop.set()
        log("  HALT (e-stop) -> bus_servo.stop(1..3)")
        with _lock:
            _send("arm.bus_servo.stop(1);arm.bus_servo.stop(2);arm.bus_servo.stop(3)")
    elif func == 0x7E:                            # CLEAR (e-stop release) — optional in-band signal
        # C12: optional in-band CLEAR. The file-absence path (estop_watcher) also
        # clears; this just releases the flag in-band. The relay does NOT touch
        # the latch file (route_clear owns its deletion).
        _estop.clear()
        log("  CLEAR (e-stop release) -> resuming")
    else:
        log("  func=0x%02x len=%d -> skip" % (func, len(data)))

while True:                                       # each FIFO line = one hex-encoded AA-55 frame
    try:
        with open(FIFO, "r") as f:                # TEXT: hex digits only, no NUL bytes to drop
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    frame = bytes(int(h, 16) for h in line.split())
                except ValueError:
                    log("  bad hex line: %r" % line[:48]); continue
                if len(frame) < 5 or frame[0] != 0xAA or frame[1] != 0x55:
                    log("  not AA-55: %r" % line[:48]); continue
                fn = frame[2]; ln = frame[3]
                if len(frame) < 4 + ln + 1:
                    log("  short frame: %r" % line[:48]); continue
                d = frame[4:4 + ln]; cks = frame[4 + ln]
                calc = (255 - ((fn + ln + sum(d)) % 256)) % 256
                if cks != calc:
                    log("  BAD CKSUM (got 0x%02x want 0x%02x) -> drop" % (cks, calc)); continue
                handle(fn, d)
    except Exception as e:
        log("relay err: %s" % e); time.sleep(0.3)
