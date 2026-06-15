#!/usr/bin/env python3
"""replay_chain.py -- re-issue every commanded pose from a chain.

Companion to verify_chain.py. Verification proves the chain wasn't
tampered with; replay proves the chain captured the complete commanded
sequence -- you can reproduce the exact arm trajectory.

Skips control actions (estop/clear/program_start/program_stop), the
unreachable reach_unreachable entries, and OUTCOME entries
(observed/observed_relaxed -- they record what the arm DID, not a
command to re-issue). Optionally rate-limits.

Usage:
    tools/replay_chain.py                             # replay against localhost
    tools/replay_chain.py --speed 0.5                 # half speed
    tools/replay_chain.py --start 50 --end 100        # subrange
    tools/replay_chain.py --file <jsonl>              # offline source
    tools/replay_chain.py --dry-run                   # print, don't fire
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request


REPLAYABLE = {"pose", "reach", "home", "poses_load", "nozzle", "suction"}

# OUTCOME entries record what the arm DID (a readback), not a command to
# re-issue. They must NEVER be replayed -- replaying an observed readback as if
# it were a commanded pose would actuate the arm to a sensor reading. Kept as an
# explicit deny-set (belt-and-suspenders on REPLAYABLE membership).
OUTCOME_KINDS = {"observed", "observed_relaxed"}


def get(base, path, **params):
    qs = urllib.parse.urlencode(params)
    url = f"{base}{path}?{qs}" if qs else f"{base}{path}"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def load_chain_from_url(url):
    with urllib.request.urlopen(f"{url}/chain", timeout=5) as r:
        return json.load(r)


def load_chain_from_file(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:7071")
    p.add_argument("--file", help="JSONL chain file (alternative to --url)")
    p.add_argument("--speed", type=float, default=1.0, help="time multiplier (0.5 = half-speed)")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=-1)
    p.add_argument("--dry-run", action="store_true", help="print actions, don't fire")
    args = p.parse_args()

    chain = load_chain_from_file(args.file) if args.file else load_chain_from_url(args.url)
    if args.end < 0:
        args.end = len(chain)
    subset = chain[args.start:args.end]

    print(f"replaying {len(subset)} entries (speed={args.speed}x, dry_run={args.dry_run})")

    # Reset to home before replay so trajectories start consistently
    if not args.dry_run:
        try:
            get(args.url, "/home", time_ms=int(500 / args.speed))
            time.sleep(0.6 / args.speed)
        except Exception as e:
            print(f"WARN: home reset failed: {e}", file=sys.stderr)

    fired = 0
    skipped = 0
    base_step_ms = int(400 / args.speed)
    inter_step_s = 0.3 / args.speed

    for entry in subset:
        idx = entry["idx"]
        kind = entry["kind"]
        state = entry["state"]
        p1, p2, p3, p4_us, su = (int(x) for x in state.split(","))

        if kind in OUTCOME_KINDS or kind not in REPLAYABLE:
            print(f"  [{idx:3d}] skip   {kind}")
            skipped += 1
            continue

        if kind == "suction":
            cmd = {0: "off", 1: "on", 2: "vent"}.get(su, "off")
            print(f"  [{idx:3d}] suction {cmd}")
            if not args.dry_run:
                get(args.url, "/suction", cmd=cmd)
        else:
            print(f"  [{idx:3d}] {kind:11s} p=({p1},{p2},{p3}) nozzle={p4_us}us")
            if not args.dry_run:
                get(args.url, "/pose", p1=p1, p2=p2, p3=p3, p4_us=p4_us, time_ms=base_step_ms)

        fired += 1
        if not args.dry_run:
            time.sleep(inter_step_s)

    print()
    print(f"=== replayed {fired}, skipped {skipped} ===")


if __name__ == "__main__":
    main()
