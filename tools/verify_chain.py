#!/usr/bin/env python3
"""verify_chain.py -- independently re-derive every chain entry's SHA
and verify the prev_sha linkage.

This is the proof side of the substrate thesis: anyone with the JSON
chain can reconstruct each entry's canonical string, SHA-256 it, and
confirm the stored hash matches byte-for-byte.

Usage:
    tools/verify_chain.py                          # against http://localhost:7071
    tools/verify_chain.py --url <base>
    tools/verify_chain.py --file ~/.ledatic-arm/chain/armsim_chain.jsonl
"""
import argparse
import hashlib
import json
import sys
import urllib.request


def load_chain_from_url(url):
    with urllib.request.urlopen(f"{url}/chain", timeout=5) as r:
        return json.load(r)


def load_chain_from_file(path):
    """Read the JSONL file directly (one entry per line)."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def canonical(entry):
    return f"{entry['prev_sha']}|{entry['t']}|{entry['kind']}|{entry['params']}|{entry['state']}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:7071")
    p.add_argument("--file", help="JSONL chain file (alternative to --url)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    chain = load_chain_from_file(args.file) if args.file else load_chain_from_url(args.url)
    n = len(chain)
    print(f"verifying {n} chain entries...")

    prev = "genesis"
    ok = 0
    bad = 0
    for entry in chain:
        idx = entry["idx"]
        prev_sha = entry["prev_sha"]
        stored_sha = entry["sha"]

        # 1. Linkage
        if prev_sha != prev:
            print(f"  [FAIL] idx={idx}: prev_sha={prev_sha[:16]}.. expected={prev[:16]}..")
            bad += 1
            continue

        # 2. Hash
        c = canonical(entry)
        derived = hashlib.sha256(c.encode("utf-8")).hexdigest()
        if derived != stored_sha:
            print(f"  [FAIL] idx={idx}: derived={derived[:16]}.. stored={stored_sha[:16]}..")
            print(f"         canonical: {c}")
            bad += 1
            continue

        if args.verbose:
            print(f"  [ok]   idx={idx:3d}  {entry['kind']:18s}  sha={stored_sha[:12]}..")

        ok += 1
        prev = stored_sha

    print()
    if bad == 0:
        print(f"=== {ok}/{n} entries verified — chain intact ===")
        sys.exit(0)
    else:
        print(f"=== {ok}/{n} ok, {bad} BROKEN ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
