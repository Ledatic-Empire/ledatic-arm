#!/usr/bin/env python3
"""repl_relay.py -- translate the sim's AA-55 bridge frames into MicroPython
REPL commands for a factory-firmware MaxArm.

The ledatic-arm sim (armsim.rail) writes AA-55 framed bytes to the bridge FIFO
(the documented Hiwonder host protocol). BUT the physical MaxArm in hand runs
the *factory MicroPython REPL* firmware @115200 (see memory
maxarm-firmware-reconciliation-2026-06-07), which does NOT parse AA-55 — it
wants Python text like `arm.set_position((x,y,z),dur)`. This relay sits between
them: it parses each AA-55 frame off the FIFO and re-emits the equivalent REPL
line over the serial port (held open at 115200, reopening the FIFO on EOF).

Translations (v1):
  0x01 SET_ANGLE  p1,p2,p3,t  -> arm.set_servo(1,p1,t);arm.set_servo(2,p2,t);arm.set_servo(3,p3,t)
  0x03 SET_XYZ    x,y,z,t     -> arm.set_position((x,y,z),t)
  0x05 SET_PWMSERVO           -> logged, NOT sent (wrist API unconfirmed — v2)
  0x07 SET_SUCTION            -> logged, NOT sent (nozzle API unconfirmed — v2)
  0x11/0x13 READ_*           -> ignored (this bridge is one-way)

Usage:  repl_relay.py <serial_dev> <baud=115200> <fifo>
"""
import os, sys, time, json, serial

DEV  = sys.argv[1]
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
FIFO = sys.argv[3]

def log(m):
    sys.stderr.write(m + "\n"); sys.stderr.flush()

# ── Safe operating ranges — the SAFETY CLAMP (every command bounded here) ──
# CONSERVATIVE defaults; refine empirically and drop the result in
# arms/safe_limits.json (loaded below). j1 (base_yaw) has NO firmware clamp —
# its only stop is the wiring loom, so it is the one that STALLS when a program
# sweeps it to 200/800. It MUST be bounded here. j2/j3 are firmware-clamped but
# we stay well inside to avoid over-extension / self-collision.
SAFE_PULSE = {1: (350, 650), 2: (400, 660), 3: (475, 660)}   # base, shoulder, elbow
SAFE_WRIST_US = (700, 2300)                                   # nozzle PWM (v2)
SAFE_XYZ = {"x": (-130, 130), "y": (-230, -90), "z": (120, 260)}  # conservative box ~ORIGIN(0,-163,212)
_limits_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "arms", "safe_limits.json")
try:
    if os.path.exists(_limits_path):
        _j = json.load(open(_limits_path))
        SAFE_PULSE = {int(k): tuple(v) for k, v in _j.get("servo_pulse", SAFE_PULSE).items()}
        SAFE_WRIST_US = tuple(_j.get("wrist_us", SAFE_WRIST_US))
        SAFE_XYZ = {k: tuple(v) for k, v in _j.get("xyz", SAFE_XYZ).items()}
        log("safe limits loaded from %s" % _limits_path)
except Exception as e:
    log("safe_limits.json load failed (%s) — using built-in conservative defaults" % e)
log("SAFE pulse=%s  xyz=%s" % (SAFE_PULSE, SAFE_XYZ))

def clamp_pulse(servo, p):
    lo, hi = SAFE_PULSE.get(servo, (0, 1000))
    c = max(lo, min(hi, p))
    if c != p: log("  CLAMP servo%d %d -> %d (safe %d..%d)" % (servo, p, c, lo, hi))
    return c

def clamp_axis(axis, v):
    lo, hi = SAFE_XYZ.get(axis, (-9999, 9999))
    c = max(lo, min(hi, v))
    if c != v: log("  CLAMP %s %d -> %d (safe %d..%d)" % (axis, v, c, lo, hi))
    return c

ser = serial.Serial(DEV, BAUD, timeout=0.5)
time.sleep(2.5)                      # ESP32 reboots when the port opens
ser.reset_input_buffer()
ser.write(b"\r\n"); ser.flush(); time.sleep(0.3); ser.reset_input_buffer()
log("repl-relay: %s @%d, FIFO=%s (AA-55 -> REPL)" % (DEV, BAUD, FIFO))

def le16(lo, hi):
    v = lo | (hi << 8)
    return v - 65536 if v >= 32768 else v   # signed (XYZ); pulses 0..1000 unaffected

def send(line):
    ser.reset_input_buffer()
    ser.write((line + "\r\n").encode()); ser.flush()
    t0 = time.time(); buf = b""
    while time.time() - t0 < 0.4:        # bounded drain; set_* returns fast, moves run async
        b = ser.read(128)
        if b: buf += b
        if buf.endswith(b">>> "): break
    return buf

def handle(func, data):
    if func == 0x01 and len(data) == 8:          # SET_ANGLE
        p1 = clamp_pulse(1, le16(data[0], data[1])); p2 = clamp_pulse(2, le16(data[2], data[3]))
        p3 = clamp_pulse(3, le16(data[4], data[5])); t  = le16(data[6], data[7])
        cmd = "arm.set_servo(1,%d,%d);arm.set_servo(2,%d,%d);arm.set_servo(3,%d,%d)" % (p1, t, p2, t, p3, t)
        log("  SET_ANGLE -> p=(%d,%d,%d) t=%d" % (p1, p2, p3, t)); send(cmd)
    elif func == 0x03 and len(data) == 8:        # SET_XYZ
        x = clamp_axis("x", le16(data[0], data[1])); y = clamp_axis("y", le16(data[2], data[3]))
        z = clamp_axis("z", le16(data[4], data[5])); t = le16(data[6], data[7])
        cmd = "arm.set_position((%d,%d,%d),%d)" % (x, y, z, t)
        log("  SET_XYZ -> (%d,%d,%d) t=%d" % (x, y, z, t)); send(cmd)
    elif func == 0x05:                            # SET_PWMSERVO (wrist)
        log("  SET_PWMSERVO %s -> skipped (v1)" % (data.hex(),))
    elif func == 0x07:                            # SET_SUCTION
        log("  SET_SUCTION %s -> skipped (v1)" % (data.hex(),))
    else:
        log("  func=0x%02x len=%d -> skip" % (func, len(data)))

buf = bytearray()
while True:
    try:
        with open(FIFO, "rb") as f:               # blocks until a writer opens
            for chunk in iter(lambda: f.read(256), b""):
                buf.extend(chunk)
                while True:                       # drain all complete frames in buf
                    i = buf.find(b"\xAA\x55")
                    if i < 0:
                        if len(buf) > 1: del buf[:-1]   # keep a trailing lone 0xAA
                        break
                    if i > 0: del buf[:i]
                    if len(buf) < 5: break          # need AA 55 FUNC LEN ...
                    func = buf[2]; length = buf[3]
                    total = 4 + length + 1          # AA 55 FUNC LEN data[length] CHKSUM
                    if len(buf) < total: break      # wait for the rest
                    data = bytes(buf[4:4 + length])
                    del buf[:total]
                    handle(func, data)
    except Exception as e:
        log("relay err: %s" % e); time.sleep(0.3)
