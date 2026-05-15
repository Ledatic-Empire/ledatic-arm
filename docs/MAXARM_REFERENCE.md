# MaxArm — technical reference

Everything we know about the Hiwonder MaxArm (a.k.a. LewanSoul MaxArm)
before delivery day. Authored 2026-05-15 from
[github.com/Hiwonder/MaxArm](https://github.com/Hiwonder/MaxArm) and
[docs.hiwonder.com](https://docs.hiwonder.com/projects/MaxArm/en/latest/).

The companion config is [`arms/maxarm.json`](../arms/maxarm.json).
Confidence ratings use `[CONFIRMED]` (cited in firmware source or
official docs), `[INFERRED]` (extrapolated from related Hiwonder
products), `[UNKNOWN]` (sniff on delivery).

## Overview

4-DOF ESP32-based desktop arm with a pneumatic suction-cup
end-effector. Three Hiwonder HTS-35H bus servos drive base yaw,
shoulder, and elbow. One Hiwonder LFD-01M PWM micro servo rotates the
suction nozzle. A 12V/5A wall adapter powers the whole thing through a
barrel jack. Firmware ships as either MicroPython or Arduino C++; the
ESP32 exposes a documented host protocol over USB-serial, BLE, or WiFi.

## Mechanical

| Item | Value | Confidence |
|---|---|---|
| DOF | 4 (3 bus + 1 PWM nozzle) | `[CONFIRMED]` |
| Overall (L x W x H) | 158 x 160 x 260 mm | `[CONFIRMED]` ThinkRobotics |
| Weight | 1.3 kg | `[CONFIRMED]` |
| Payload | 450 g (1 lb) suction max | `[CONFIRMED]` Amazon listing |
| Outer reach (xy) | ~290 mm | `[INFERRED]` from link sum |
| Z ceiling | 225 mm (hard) | `[CONFIRMED]` espmax.py |
| Inner xy radius | 50 mm singularity floor | `[CONFIRMED]` espmax.py |

### Link lengths (firmware-canonical)

From `Arduino/.../_espmax.h`:

```
L0 = 84.4 mm   base column / pedestal height
L1 =  8.14 mm  offset from base axis to shoulder pivot
L2 = 128.4 mm  upper arm   (shoulder -> elbow)
L3 = 138.0 mm  forearm     (elbow    -> wrist)
L4 =  16.8 mm  wrist       -> suction-tip horizontal offset
```

`Python/.../espmax.py` has slightly rounded values (84.0 / 8.2 / 128.0).
Treat Arduino values as canonical for FK/IK; verify with calipers on
delivery.

### Home pose

```
joint_pulses    = (500, 500, 500)    # midpoint of each bus servo
nozzle_pulse_us = 1500               # midpoint of LFD-01M PWM
tip_mm          = (0, -163, 212)     # ORIGIN = (0, -(L1+L3+L4), L0+L2)
joint_degrees   = (120, 90, 0)       # base, shoulder, elbow at home
```

## Joints

| ID | Name | Axis | Servo | Pulse range | Pulse home | Degrees | Notes |
|---|---|---|---|---|---|---|---|
| 1 | base_yaw | Z | HTS-35H bus id=1 | 0-1000 | 500 | 0-240 | No firmware clamp; cable-loom limit unknown |
| 2 | shoulder | Y | HTS-35H bus id=2 | 0-700 | 500 | 0-168 | Firmware clamps p>700 -> 700 |
| 3 | elbow | Y | HTS-35H bus id=3 | 470-1000 | 500 | 113-240 | Firmware clamps p<470 -> 470 |
| 4 | nozzle_wrist | tool roll | LFD-01M PWM | 500-2500 us | 1500 | -90 to +90 | PWM @ 50 Hz on GPIO15 |

Bus-servo conversion: **1 pulse = 0.24 degrees**, range 0-1000 maps to
0-240 degrees.

## Servos

### HTS-35H — bus servo (joints 1, 2, 3)

[hiwonder.com/products/hts-35h](https://www.hiwonder.com/products/hts-35h)

| Spec | Value |
|---|---|
| Torque | 35 kg.cm @ 11.1V |
| Voltage | 9 - 12.6V |
| Angular range | 240 degrees |
| Pulse range | 0-1000 (1 unit = 0.24 deg) |
| Angular accuracy | 0.2 deg |
| Speed | 0.18 s / 60 deg @ 11.1V |
| No-load current | 100 mA |
| Stall current | 3 A |
| Bus | half-duplex UART, 115200 8N1 |
| Default ID | 1 (configurable 0-253) |
| Feedback | position, temperature, input voltage |
| Weight | 64 g |

### LFD-01M — PWM micro servo (joint 4, nozzle rotation)

[hiwonder.com/products/lfd-01m](https://www.hiwonder.com/products/lfd-01m)

| Spec | Value |
|---|---|
| Bus | hobby 50 Hz PWM |
| Voltage | 4.8 - 6V |
| Rotation | 0-180 degrees (firmware uses +/-90 around center) |
| Pulse width | 500 - 2500 us |
| Torque | >= 1.8 kg.cm @ 6V |
| Speed | 0.12 s / 60 deg @ 6V |
| Weight | 14 g |
| Gear | metal |

## End-effector — suction

Architecture: vacuum pump + solenoid vent valve + nozzle-rotation PWM
servo. Open-loop (no vacuum sensor). Pump runs on demand; solenoid acts
as a release vent to drop a held object.

**Pump module** `[INFERRED]` matches Hiwonder air-pump-module spec:

- Motor: small DC diaphragm pump (~370-series)
- Rated voltage: 6V (rail derived from 12V through the H-bridge driver)
- Rated current: < 420 mA
- Max vacuum: > -350 mmHg
- Operating pressure: 400-650 mmHg
- Flow rate: 1.8-2.5 LPM

**Control mechanism** (CONFIRMED from `SuctionNozzle.py`):

```python
pump_io  = [21, 19]   # M1 — H-bridge forward / backward
valve_io = [18,  5]   # M2 — solenoid forward / backward
pwm_hz   = 1000
```

- **Pump ON:** PWM `pump_f` = max (1000/1000), all else 0. Pulls vacuum.
- **Pump OFF / release:** PWM `valve_f` = max for 1000 ms (vent), then
  valve_f = 0 (close). Runs on background thread.

## Wire protocols

MaxArm has two protocol layers. Treat them as separate concerns.

### Outer protocol — host <-> ESP32

This is what `protocols/lewansoul.rail` will speak. Documented at
[docs.hiwonder.com/.../10.MaxArm_Serial_Communication](https://docs.hiwonder.com/projects/MaxArm/en/latest/_sources/docs/10.MaxArm_Serial_Communication_formatted.md.txt).

**Frame:**

```
| 0xAA | 0x55 | FUNC | LEN | DATA[N] | CHKSUM |
```

- Header: literal `0xAA 0x55`
- FUNC: 1-byte function code
- LEN: 1 byte = number of DATA bytes (N)
- DATA: N bytes, little-endian for 16-bit values, two's-complement for signed
- CHKSUM: 1 byte = `~(FUNC + LEN + sum(DATA)) & 0xFF`

**Transports:**

- **USB serial:** 9600 baud, 8N1, `/dev/ttyUSB0` or `/dev/cu.usbserial-*`
- **BLE:** device name `MaxArm`, likely Nordic UART Service (NUS) but UUIDs `[UNKNOWN]` — confirm with `nRF Connect` on day one
- **WiFi:** AP mode default, SSID prefix `HW...`, password `hiwonder`, device IP `192.168.149.1`. **Port `[UNKNOWN]`** — sniff with nmap (likely 6000, 8888, or 9000)

**Function codes:**

| Hex | Name | Direction | DATA |
|---|---|---|---|
| 0x01 | SET_ANGLE | M->S | 8 B: p1_le16, p2_le16, p3_le16, time_ms_le16 |
| 0x03 | SET_XYZ | M->S | 8 B: x, y, z (signed int16 mm), time_ms_le16 |
| 0x05 | SET_PWMSERVO | M->S | 4 B: pulse_us_le16, time_ms_le16 |
| 0x07 | SET_SUCTIONNOZZLE | M->S | 1 B: 0x01=pump on, 0x02=vent open, 0x03=vent close |
| 0x11 | READ_ANGLE | M->S=0 / S->M=6 B (3x int16 pulses) |
| 0x13 | READ_XYZ | M->S=0 / S->M=6 B (3x int16 mm) |

**Examples (verbatim from official docs):**

```
Set servos to (200, 500, 500), 2000 ms:
  AA 55 01 08 C8 00 F4 01 F4 01 D0 07 6D

Set XYZ to (120, -180, 85), 1000 ms:    (-180 = 0xFF4C two's complement)
  AA 55 03 08 78 00 4C FF 55 00 E8 03 F1

Pump on:           AA 55 07 01 01 F7
Vent open:         AA 55 07 01 02 F6
Vent close:        AA 55 07 01 03 F5

Query pulses:      AA 55 11 00 EE
  Response (873, 410, 713):
                   AA 55 11 06 60 03 9A 01 C9 02 20

Query XYZ:         AA 55 13 00 EC
  Response (-159, -6, 96):
                   AA 55 13 06 61 FF FA FF 60 00 2E
```

### Inner protocol — ESP32 <-> HTS-35H bus servo

You usually do NOT speak this from the host — the firmware abstracts it
behind the outer protocol. But the sim's "fake firmware" mode may need
to honor it.

**Frame:**

```
| 0x55 | 0x55 | ID | LEN | CMD | PARAMS[0-4] | CHKSUM |
```

- Header: `0x55 0x55`
- ID: servo id (1-253; 0xFE = broadcast)
- LEN: `3 + N` (LEN + CMD + N params + CHKSUM)
- CMD: see table below
- PARAMS: 16-bit values little-endian
- CHKSUM: `~(ID + LEN + CMD + sum(PARAMS)) & 0xFF`

UART: 115200 baud, 8N1, half-duplex with TX_EN/RX_EN GPIOs.

Command set is the standard LewanSoul/Lobot bus protocol — see
[madhephaestus/lx16a-servo](https://github.com/madhephaestus/lx16a-servo)
for the full table (CMDs 0x01-0x24).

## ESP32 pin map

From firmware constants in `BusServo.py`, `PWMServo.py`,
`SuctionNozzle.py`:

```
Bus servo TX        GPIO 12
Bus servo RX        GPIO 35
Bus servo TX_EN     GPIO 14
Bus servo RX_EN     GPIO 13
PWM nozzle servo    GPIO 15
PWM spare           GPIO  4
Pump H-bridge       GPIO 21, 19
Valve H-bridge      GPIO 18,  5
USB UART TX         GPIO 10
USB UART RX         GPIO 34
I2C SCL             GPIO 16
I2C SDA             GPIO 17
BLE LED             GPIO 26
BLE key             GPIO 25
```

## Power

- Wall adapter: 12V / 5A DC (60 W), barrel jack
- Servo rail: 11.1V nominal (HTS-35H range 9-12.6V)
- Pump rail: ~6V (PWM-modulated from 12V via H-bridge)
- PWM-servo rail: 4.8-6V
- ESP32 + logic: 3.3V (onboard regulator)
- No battery — wall-tethered
- Active current peak `[INFERRED]`: ~1.5-2 A from the 12V rail

## Python firmware classes

These are what runs on the ESP32. They tell you what the wire protocol
exposes, since the outer protocol just relays calls to these classes.

```python
# BusServo.py
class BusServo:
    def run(self, id, pulse, time_ms=1000)            # cmd 0x01
    def stop(self, id)                                 # cmd 0x0C
    def get_position(self, id)                         # cmd 0x1C
    def load(self, id)        # torque on              # cmd 0x1F
    def unload(self, id)      # torque off (teaching)  # cmd 0x1F
    def get_vin(self, id)                              # cmd 0x1B

# espmax.py — top-level kinematics
class ESPMax:
    ORIGIN = (0, -163.0, 212.0)
    def set_position(self, position, duration)         # IK + set pulses
    def set_position_with_speed(self, position, speed)
    def set_servo(self, servo_id, pulse, duration)
    def set_joint(self, joint_id, angle, duration)
    def go_home(self, duration=2000)
    def teaching_mode(self)                            # unloads all 3 bus servos
    def read_position(self)                            # FK from current pulses

# SuctionNozzle.py
class SuctionNozzle:
    def on(self)                                       # pump on
    def off(self)                                      # vent 1 s then close
    def set_angle(self, angle=0, duration=1000)        # nozzle PWM ±90 deg
```

## Open questions for delivery day

Sniff or measure these within the first 2 hours:

1. WiFi port number — try 6000, 8888, 9000, 23, 80 with `nmap 192.168.149.1`
2. BLE GATT UUIDs — `nRF Connect` on phone; likely NUS service
   `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
3. Whether STA mode works out of the box or only AP
4. Caliper measurements for L0-L4, confirm against 84.4 / 8.14 / 128.4 / 138.0 / 16.8
5. J1 base yaw cable-bind limit — firmware doesn't clamp; physical limit
   is wiring loom. Sweep at 50-pulse intervals.
6. Baseline `BusServo.get_offset(id)` per bus servo — factory offset
7. `BusServo.get_vin(id)` at idle and under load — confirm power rail
8. Multimeter pump rail at M1 connector — expect ~6V at 100% duty
9. Round-trip latency for `AA 55 11 00 EE` -> response — expect 5-15 ms
   on USB, 50-200 ms on BLE
10. Whether the firmware acknowledges every Style-A command or only the
    reads
11. Endianness verification on FUNC_SET_XYZ — send one positive and one
    negative example
12. Action-group file format and how to push `*.rob` files to ESP32
    flash

## Sources

- [github.com/Hiwonder/MaxArm](https://github.com/Hiwonder/MaxArm) — firmware (C++ + Python)
- [MaxArm v1.0 docs](https://docs.hiwonder.com/projects/MaxArm/en/latest/)
- [MaxArm Serial Communication Protocol](https://docs.hiwonder.com/projects/MaxArm/en/latest/_sources/docs/10.MaxArm_Serial_Communication_formatted.md.txt)
- [MaxArm Inverse Kinematics](https://wiki.hiwonder.com/projects/MaxArm/en/latest/docs/8.Inverse_Kinematics_Basic_andApplication_formatted.html)
- [Hiwonder MaxArm product page](https://www.hiwonder.com/products/maxarm)
- [ThinkRobotics MaxArm spec page](https://thinkrobotics.com/products/maxarm-open-source-robot-arm-powered-by-esp32)
- [HTS-35H servo spec](https://www.hiwonder.com/products/hts-35h)
- [LFD-01M servo spec](https://www.hiwonder.com/products/lfd-01m)
- [Hiwonder air pump module](https://www.hiwonder.com/products/air-pump-module)
- [LewanSoul MaxArm Amazon listing](https://www.amazon.com/LewanSoul-Bluetooth-Connection-Programming-Education/dp/B0CG5YJKQV)
- [madhephaestus/lx16a-servo](https://github.com/madhephaestus/lx16a-servo) — inner bus protocol reference
- [migsdigs/Hiwonder_xArm_ESP32](https://github.com/migsdigs/Hiwonder_xArm_ESP32) — sibling-arm SDK
