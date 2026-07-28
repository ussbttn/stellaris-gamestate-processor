"""
schema_diff.py — map a Stellaris gamestate's structure, and diff two of them.

Purpose: replace guessing with measurement. Field names lie; cardinality and
change-over-time do not. Two saves taken around a known action isolate which
path records that action.

    python schema_diff.py schema  <gamestate>            > a.json
    python schema_diff.py diff    a.json b.json          # what changed
    python schema_diff.py find    <gamestate> 604        # paths sized ~604

Accepts raw `gamestate` text or a `.sav` (zip) directly.

Line-oriented on purpose: Paradox saves are tab-indented, so tracking depth by
indentation is far faster than character-level brace matching, which matters on
a 48MB file.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

KEY_BLOCK = re.compile(r"^(\t*)([A-Za-z_][\w.]*)=\s*$")
KEY_VALUE = re.compile(r"^(\t*)([A-Za-z_][\w.]*)=(.*)$")
OPEN_B = re.compile(r"^\t*\{\s*$")
CLOSE_B = re.compile(r"^\t*\}\s*$")
NUM_KEY = re.compile(r"^(\t*)(\d+)=\s*$")


def load(path: str) -> str:
    p = Path(path)
    if p.suffix == ".sav":
        with zipfile.ZipFile(p) as z:
            return z.read("gamestate").decode("utf-8", "replace")
    return p.read_text(encoding="utf-8", errors="replace")


def schema(text: str, max_depth: int = 7) -> dict:
    """Map path -> {count, kind, sample}.

    Numeric-keyed children collapse to a count under `[]`, so 604 systems
    produce one entry with count 604 rather than 604 separate paths. Cardinality
    is the signal we care about.
    """
    out: dict[str, dict] = {}
    counts: dict[str, int] = defaultdict(int)
    stack: list[str] = []
    scalars: dict[str, int] = defaultdict(int)

    for raw in text.split("\n"):
        if not raw or raw.isspace():
            continue
        if CLOSE_B.match(raw):
            if stack:
                stack.pop()
            continue
        if OPEN_B.match(raw):
            continue

        m = NUM_KEY.match(raw)
        if m:
            depth = len(m.group(1))
            del stack[depth:]
            parent = ".".join(stack)
            counts[parent + ".[]" if parent else "[]"] += 1
            stack.append("[]")
            continue

        m = KEY_BLOCK.match(raw)
        if m:
            depth, key = len(m.group(1)), m.group(2)
            del stack[depth:]
            stack.append(key)
            if len(stack) <= max_depth:
                path = ".".join(stack)
                out.setdefault(path, {"kind": "block", "n": 0})
                out[path]["n"] += 1
            continue

        m = KEY_VALUE.match(raw)
        if m:
            depth, key, val = len(m.group(1)), m.group(2), m.group(3).strip()
            del stack[depth:]
            path = ".".join(stack + [key])
            if len(stack) < max_depth:
                e = out.setdefault(path, {"kind": "scalar", "n": 0, "sample": val[:60]})
                e["n"] += 1
            continue

        # bare tokens inside a braced list -> array members
        parent = ".".join(stack)
        if parent and len(stack) <= max_depth:
            scalars[parent] += len(raw.split())

    for p, n in counts.items():
        out.setdefault(p, {"kind": "list", "n": 0})
        out[p]["n"] = n
        out[p]["kind"] = "list"
    for p, n in scalars.items():
        e = out.setdefault(p, {"kind": "array", "n": 0})
        if e["kind"] in ("block", "array"):
            e["kind"] = "array"
            e["len"] = n
    return out


def cmd_schema(path: str) -> None:
    s = schema(load(path))
    json.dump(s, sys.stdout, indent=0, sort_keys=True)
    print(f"\n# {len(s):,} paths", file=sys.stderr)


def cmd_find(path: str, target: int, tol: float = 0.02) -> None:
    """Find paths whose size is close to a known count.

    The point: if you know there are 604 systems, every path sized ~604 is a
    candidate for 'indexed by system'. This is the check that would have caught
    the survey error, and it takes one command.
    """
    s = schema(load(path))
    lo, hi = target * (1 - tol) - 2, target * (1 + tol) + 2
    hits = []
    for p, e in s.items():
        for field in ("len", "n"):
            v = e.get(field)
            if v and lo <= v <= hi:
                hits.append((p, field, v))
    hits.sort(key=lambda h: -h[2])
    print(f"paths sized ~{target} (+/-{tol:.0%}):")
    for p, f, v in hits[:60]:
        print(f"  {v:>8,}  {f:<4}  {p}")
    print(f"\n{len(hits)} matches")


def cmd_diff(a_path: str, b_path: str) -> None:
    a = json.load(open(a_path)) if a_path.endswith(".json") else schema(load(a_path))
    b = json.load(open(b_path)) if b_path.endswith(".json") else schema(load(b_path))

    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = []
    for p in set(a) & set(b):
        for field in ("len", "n"):
            va, vb = a[p].get(field), b[p].get(field)
            if va is not None and vb is not None and va != vb:
                changed.append((abs(vb - va), p, field, va, vb))
    changed.sort(reverse=True)

    print(f"paths: {len(a):,} -> {len(b):,}   +{len(added)} -{len(removed)}\n")
    if added:
        print("APPEARED:")
        for p in added[:25]:
            print(f"  {p}")
    if removed:
        print("\nVANISHED:")
        for p in removed[:25]:
            print(f"  {p}")
    print(f"\nCHANGED SIZE (top 40 of {len(changed)}):")
    for d, p, f, va, vb in changed[:40]:
        sign = "+" if vb > va else ""
        print(f"  {sign}{vb-va:>8,}  {va:>9,} -> {vb:<9,} {f:<4} {p}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "schema":
        cmd_schema(sys.argv[2])
    elif cmd == "find":
        cmd_find(sys.argv[2], int(sys.argv[3]))
    elif cmd == "diff":
        cmd_diff(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
