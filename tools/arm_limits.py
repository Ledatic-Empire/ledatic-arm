#!/usr/bin/env python3
# arm_limits.py -- empirical per-servo safe-range finder for the MaxArm.
# Jogs each bus servo out from home in small steps, watching get_position; when
# actual stops tracking commanded (hard stop) it records the last-good value.
# Writes nothing -- prints RESULTS=<json>; paste the safe ranges into
# arms/safe_limits.json. Caps in CAPS keep it clear of the table; get_position
# senses servo stalls, NOT soft collisions, so WATCH THE ARM. See docs/REAL_ARM_OPERATION.md.
import serial, time, json
DEV="/dev/cu.usbserial-210"
ser=serial.Serial(DEV,115200,timeout=0.4); time.sleep(2.5); ser.reset_input_buffer()
ser.write(b"\r\n"); ser.flush(); time.sleep(0.3); ser.reset_input_buffer()
def cmd(line, rs=2.0):
    ser.reset_input_buffer(); ser.write((line+"\r\n").encode()); ser.flush()
    t0=time.time(); buf=b""
    while time.time()-t0<rs:
        b=ser.read(256)
        if b: buf+=b
        if buf.endswith(b">>> "): break
    r=buf.decode("utf-8","replace").replace("\r","").strip()
    if r.startswith(line): r=r[len(line):].strip()
    if r.endswith(">>>"): r=r[:-3].strip()
    return r
def getpos(s):
    try: return int(cmd("print(arm.bus_servo.get_position(%d))"%s))
    except: return None
def runto(s,p,t=350): cmd("arm.bus_servo.run(%d,%d,%d)"%(s,p,t))

HOME=500; STEP=22; TOL=20; DWELL=0.7; MARGIN=12; MAXSTEPS=26
# Conservative caps: never exceed firmware clamps; keep 2/3 clear of the table.
CAPS={1:(150,850), 2:(330,690), 3:(470,720)}   # base / shoulder / elbow

def find_dir(s, d):
    runto(s,HOME,700); time.sleep(0.9)
    lo,hi=CAPS[s]; last=HOME; c=HOME
    for _ in range(MAXSTEPS):
        c += d*STEP
        if c<lo or c>hi:
            runto(s,HOME,700); time.sleep(0.9)
            return (max(lo,min(hi,last)), "cap")
        runto(s,c,300); time.sleep(DWELL)
        a=getpos(s)
        if a is None:
            runto(s,HOME,700); time.sleep(0.9); return (last,"noread")
        if abs(a-c)>TOL:                       # stalled — hard stop
            runto(s,last,350); time.sleep(0.4)
            runto(s,HOME,700); time.sleep(0.9)
            return (last, "stall@%d"%a)
        last=a
    runto(s,HOME,700); time.sleep(0.9)
    return (last,"maxsteps")

res={}
for s in [1,2,3]:
    cmd("arm.bus_servo.load(%d)"%s)
    dn,dw=find_dir(s,-1)
    up,uw=find_dir(s,+1)
    # apply inward safety margin
    dn_safe=dn+MARGIN if "stall" in dw else dn
    up_safe=up-MARGIN if "stall" in uw else up
    res[s]={"down":dn,"down_why":dw,"up":up,"up_why":uw,"safe_lo":dn_safe,"safe_hi":up_safe}
    print("servo%d  down=%d(%s) up=%d(%s)  -> SAFE [%d, %d]"%(s,dn,dw,up,uw,dn_safe,up_safe))
cmd("arm.go_home()"); time.sleep(1.0)
ser.close()
print("RESULTS="+json.dumps(res))
