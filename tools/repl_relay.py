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
  0x01 SET_ANGLE  p1,p2,p3,t  -> arm.set_servo(1..3,p,t)   (loads servos if relaxed)
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

Usage:  repl_relay.py <serial_dev> <baud=115200> <fifo>
"""
import os, sys, time, json, threading, serial

DEV  = sys.argv[1]
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
FIFO = sys.argv[3]

def log(m):
    sys.stderr.write(m + "\n"); sys.stderr.flush()

# ── Safe ranges + relax policy (from arms/safe_limits.json; conservative fallback) ──
SAFE_PULSE = {1: (350, 650), 2: (400, 660), 3: (475, 660)}
SAFE_WRIST_US = (700, 2300)
SAFE_XYZ = {"x": (-130, 130), "y": (-230, -90), "z": (120, 260)}
IDLE_RELAX_SEC = 10
_limits = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arms", "safe_limits.json")
try:
    if os.path.exists(_limits):
        _j = json.load(open(_limits))
        SAFE_PULSE = {int(k): tuple(v) for k, v in _j.get("servo_pulse", SAFE_PULSE).items()}
        SAFE_WRIST_US = tuple(_j.get("wrist_us", SAFE_WRIST_US))
        SAFE_XYZ = {k: tuple(v) for k, v in _j.get("xyz", SAFE_XYZ).items()}
        IDLE_RELAX_SEC = _j.get("idle_relax_sec", IDLE_RELAX_SEC)
        log("safe limits loaded from %s" % _limits)
except Exception as e:
    log("safe_limits.json load failed (%s) -- built-in defaults" % e)
log("SAFE pulse=%s  xyz=%s  idle_relax=%ss" % (SAFE_PULSE, SAFE_XYZ, IDLE_RELAX_SEC))

def clamp_pulse(s, p):
    lo, hi = SAFE_PULSE.get(s, (0, 1000)); c = max(lo, min(hi, p))
    if c != p: log("  CLAMP servo%d %d -> %d (safe %d..%d)" % (s, p, c, lo, hi))
    return c
def clamp_axis(a, v):
    lo, hi = SAFE_XYZ.get(a, (-9999, 9999)); c = max(lo, min(hi, v))
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

def le16(lo, hi):
    v = lo | (hi << 8)
    return v - 65536 if v >= 32768 else v

# ── one lock serializes ALL REPL writes (main thread + relax watchdog) ──
_lock = threading.Lock()
_loaded = True                       # firmware boots with the bus servos loaded
_last_move = time.time()

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
    global _loaded, _last_move
    with _lock:
        if not _loaded:
            _send("arm.bus_servo.load(1);arm.bus_servo.load(2);arm.bus_servo.load(3)")
            _loaded = True; log("  LOAD (re-engage from relaxed)")
        _send(line); _last_move = time.time()

def aux_send(line):                  # wrist / suction: no load, but keep the arm awake
    global _last_move
    with _lock:
        _send(line); _last_move = time.time()

def relax_watchdog():
    global _loaded
    if IDLE_RELAX_SEC <= 0: return
    while True:
        time.sleep(2)
        with _lock:
            if _loaded and (time.time() - _last_move) > IDLE_RELAX_SEC:
                _send("arm.teaching_mode()"); _loaded = False
                log("  AUTO-RELAX (idle >%ss) -> teaching_mode (servos unloaded)" % IDLE_RELAX_SEC)

threading.Thread(target=relax_watchdog, daemon=True).start()

def handle(func, data):
    if func == 0x01 and len(data) == 8:          # SET_ANGLE
        p1 = clamp_pulse(1, le16(data[0], data[1])); p2 = clamp_pulse(2, le16(data[2], data[3]))
        p3 = clamp_pulse(3, le16(data[4], data[5])); t  = le16(data[6], data[7])
        log("  SET_ANGLE -> p=(%d,%d,%d) t=%d" % (p1, p2, p3, t))
        joint_move("arm.set_servo(1,%d,%d);arm.set_servo(2,%d,%d);arm.set_servo(3,%d,%d)" % (p1, t, p2, t, p3, t))
    elif func == 0x03 and len(data) == 8:        # SET_XYZ
        x = clamp_axis("x", le16(data[0], data[1])); y = clamp_axis("y", le16(data[2], data[3]))
        z = clamp_axis("z", le16(data[4], data[5])); t = le16(data[6], data[7])
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
        log("  HALT (e-stop) -> bus_servo.stop(1..3)")
        with _lock:
            _send("arm.bus_servo.stop(1);arm.bus_servo.stop(2);arm.bus_servo.stop(3)")
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
