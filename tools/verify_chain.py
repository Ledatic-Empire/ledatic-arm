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

Recovery / integrity (operates on the on-disk chain DIR, not HTTP):
    tools/verify_chain.py --dir ~/.ledatic-arm/chain            # verify chain + report pointer state
    tools/verify_chain.py --dir <dir> --check-pointers          # also report head/idx desync
    tools/verify_chain.py --dir <dir> --repair                  # dry-run: show what would be fixed
    tools/verify_chain.py --dir <dir> --repair --apply          # truncate a trailing torn line + resync pointers

Recovery is conservative and NEVER rewrites history:
  * only a TRAILING torn line (bad JSON or bad self-SHA / linkage on the
    LAST line) is truncated -- an interior break is real tampering and is
    a hard FAIL with no repair.
  * head/idx pointers are resynced FORWARD to the last intact entry (this
    recovers a crash-after-append-before-pointer-update).

Exit codes:
  0  intact + consistent (pointers match, no torn tail)
  2  repaired (trailing torn line truncated and/or pointers resynced under --apply)
  1  real corruption (interior hash/linkage break -- never auto-repaired),
     or a torn tail / pointer desync detected without --apply
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request


CHAIN_NAME = "armsim_chain.jsonl"
HEAD_NAME = "armsim_chain_head.txt"
IDX_NAME = "armsim_chain_idx.txt"


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


def default_chain_dir():
    """Mirror armsim.rail resolve_chain_dir: ARMSIM_EPHEMERAL=>/tmp,
    else $HOME/.ledatic-arm/chain (fallback /tmp if no HOME)."""
    if os.environ.get("ARMSIM_EPHEMERAL"):
        return "/tmp"
    home = os.environ.get("HOME")
    if not home:
        return "/tmp"
    return os.path.join(home, ".ledatic-arm", "chain")


def atomic_write(path, data):
    """Write `data` to <path>.tmp, fsync, then rename over `path`
    (atomic same-fs) -- the C6 convention, mirrored on the recovery side."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def verify_entries(chain):
    """Re-derive + verify every entry in order. Returns
    (ok_count, first_bad_idx_or_None, last_intact_entry_or_None).
    first_bad is the LIST INDEX (0-based position) of the first entry that
    fails linkage or self-SHA -- not the entry's 'idx' field."""
    prev = "genesis"
    ok = 0
    first_bad = None
    last_intact = None
    for pos, entry in enumerate(chain):
        try:
            prev_sha = entry["prev_sha"]
            stored_sha = entry["sha"]
        except (KeyError, TypeError):
            if first_bad is None:
                first_bad = pos
            return ok, first_bad, last_intact

        if prev_sha != prev:
            if first_bad is None:
                first_bad = pos
            return ok, first_bad, last_intact

        c = canonical(entry)
        derived = hashlib.sha256(c.encode("utf-8")).hexdigest()
        if derived != stored_sha:
            if first_bad is None:
                first_bad = pos
            return ok, first_bad, last_intact

        ok += 1
        prev = stored_sha
        last_intact = entry
    return ok, first_bad, last_intact


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:7071")
    p.add_argument("--file", help="JSONL chain file (alternative to --url)")
    p.add_argument("--dir", help="chain DIR (recovery/integrity mode); "
                                 "default ~/.ledatic-arm/chain, /tmp under ARMSIM_EPHEMERAL")
    p.add_argument("--check-pointers", action="store_true",
                   help="report head/idx pointer desync (implies --dir)")
    p.add_argument("--repair", action="store_true",
                   help="repair a trailing torn line + resync pointers (dry-run unless --apply)")
    p.add_argument("--apply", action="store_true",
                   help="actually perform --repair writes (otherwise dry-run)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    # Recovery / integrity mode is triggered by any of --dir / --check-pointers / --repair.
    if args.dir or args.check_pointers or args.repair:
        return recovery_mode(args)

    # ─── Legacy verify mode (unchanged) ───
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


def recovery_mode(args):
    chain_dir = args.dir or default_chain_dir()
    chain_path = os.path.join(chain_dir, CHAIN_NAME)
    head_path = os.path.join(chain_dir, HEAD_NAME)
    idx_path = os.path.join(chain_dir, IDX_NAME)
    apply = args.apply

    if not os.path.exists(chain_path):
        print(f"chain file not found: {chain_path}")
        sys.exit(1)

    # Read raw lines (keep them so we can reconstruct an intact prefix byte-for-byte).
    with open(chain_path) as f:
        raw_lines = [ln for ln in f.read().splitlines() if ln.strip()]
    total = len(raw_lines)
    print(f"recovery: {chain_path} ({total} non-empty lines)")

    # Parse as far as possible; a JSON-unparseable line is a candidate torn line.
    parsed = []
    parse_fail_pos = None
    for pos, ln in enumerate(raw_lines):
        try:
            parsed.append(json.loads(ln))
        except json.JSONDecodeError:
            parse_fail_pos = pos
            break

    # Verify the cleanly-parsed prefix.
    ok, first_bad, last_intact = verify_entries(parsed)

    # Determine where the first defect is, as a position in raw_lines.
    if parse_fail_pos is not None and (first_bad is None or parse_fail_pos <= first_bad):
        defect_pos = parse_fail_pos
        defect_kind = "json-parse"
    elif first_bad is not None:
        defect_pos = first_bad
        defect_kind = "hash/linkage"
    else:
        defect_pos = None
        defect_kind = None

    torn_tail = defect_pos is not None and defect_pos == total - 1
    interior_break = defect_pos is not None and defect_pos < total - 1

    if interior_break:
        assert defect_pos is not None   # interior_break implies a located defect
        # An earlier line is broken -> real tampering. NEVER repair history.
        print(f"  [FAIL] interior break at line {defect_pos} ({defect_kind}) "
              f"-- {total - 1 - defect_pos} intact-looking line(s) follow it.")
        print("  refusing to repair: only a TRAILING torn line is recoverable; "
              "an interior break is real corruption/tampering.")
        sys.exit(1)

    # ── Decide intact entry set + true pointers ──
    if torn_tail:
        assert defect_pos is not None   # torn_tail implies a located defect
        # The intact prefix is everything before the torn line; re-derive the true
        # pointer set from it (a hash/linkage torn tail was parsed and is dropped
        # here; a JSON-parse failure never entered `parsed` in the first place).
        intact_raw = raw_lines[:defect_pos]
        _ok2, fb2, last2 = verify_entries([json.loads(x) for x in intact_raw])
        if fb2 is not None:
            print(f"  [FAIL] after dropping the torn tail, an interior break remains "
                  f"at line {fb2}. Refusing to repair.")
            sys.exit(1)
        true_count = len(intact_raw)
        true_last = last2
        print(f"  trailing torn line detected at line {defect_pos} ({defect_kind}); "
              f"{true_count} intact entries precede it.")
    else:
        intact_raw = raw_lines
        true_count = ok
        true_last = last_intact
        print(f"  {ok}/{total} entries verified — chain body intact.")

    # ── Pointer truth ──
    if true_last is not None:
        true_head = true_last["sha"]
        true_idx = true_last["idx"]
    else:
        true_head = "genesis"
        true_idx = 0

    stored_head = _read_text(head_path)
    stored_idx_raw = _read_text(idx_path)
    stored_idx = stored_idx_raw.strip() if stored_idx_raw is not None else None

    head_desync = (stored_head if stored_head is not None else "") != ("" if true_head == "genesis" else true_head) \
        and not (true_head == "genesis" and (stored_head is None or stored_head == ""))
    # Normalize: a missing/empty head file means "genesis".
    eff_head = stored_head if (stored_head not in (None, "")) else "genesis"
    head_desync = eff_head != true_head
    eff_idx = stored_idx if (stored_idx not in (None, "")) else "0"
    idx_desync = eff_idx != str(true_idx)

    if args.check_pointers or args.repair:
        print(f"  pointers: head_file={_short(stored_head)} idx_file={eff_idx} | "
              f"true_head={_short(true_head)} true_idx={true_idx}")
        if head_desync:
            print(f"  [DESYNC] head pointer {_short(eff_head)} != true {_short(true_head)}")
        if idx_desync:
            print(f"  [DESYNC] idx pointer {eff_idx} != true {true_idx}")

    needs_repair = torn_tail or head_desync or idx_desync

    if not needs_repair:
        print(f"=== {true_count} entries intact, pointers consistent — OK ===")
        sys.exit(0)

    # ── Repair (dry-run unless --apply) ──
    if not args.repair:
        # --check-pointers without --repair: report only, but signal a problem.
        print("=== desync/torn detected — re-run with --repair --apply to fix ===")
        sys.exit(1)

    plan = []
    if torn_tail:
        plan.append(f"truncate chain to {true_count} intact line(s) (drop trailing torn line)")
    if head_desync:
        plan.append(f"rewrite head -> {_short(true_head)}")
    if idx_desync:
        plan.append(f"rewrite idx -> {true_idx}")

    if not apply:
        print("  [DRY-RUN] would:")
        for step in plan:
            print(f"    - {step}")
        print("=== dry-run: pass --apply to perform the repair ===")
        sys.exit(2)

    # Apply, in the C6 atomic order analog: rewrite chain prefix, then pointers.
    if torn_tail:
        body = "".join(ln + "\n" for ln in intact_raw)
        atomic_write(chain_path, body)
        print(f"  truncated chain to {true_count} intact line(s)")
    if head_desync:
        atomic_write(head_path, "" if true_head == "genesis" else true_head)
        print(f"  head -> {_short(true_head)}")
    if idx_desync:
        atomic_write(idx_path, str(true_idx))
        print(f"  idx -> {true_idx}")

    print(f"=== repaired: {true_count} entries intact, pointers resynced ===")
    sys.exit(2)


def _read_text(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def _short(s):
    if s is None:
        return "<none>"
    if len(s) <= 16:
        return s
    return s[:16] + ".."


if __name__ == "__main__":
    main()
