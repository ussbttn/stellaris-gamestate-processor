"""
watcher.py — watch saves, redact, drop the result into SillyTavern.

Replaces the old proxy. This process never talks to an LLM and holds no API
keys; SillyTavern keeps its own provider configuration, so every backend it
supports works with no changes here.

It writes TWO files, and the split is deliberate:

    briefing.txt  -- goes into the model's context
    audit.json    -- stays client-side, never injected

audit.json holds the names of empires the player has NOT met, so the extension
can flag a reply that mentions one. Those names are exactly the data we are
trying to keep away from the model, so they must never share a file with the
briefing. One file, one destination.

    python watcher.py --saves "~/Documents/.../save games" \
                      --out "~/SillyTavern/data/default-user/extensions/stellaris-advisor"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from extract_intel import _matching_block, read_gamestate
from game_state import _render_name, build_situation, render_briefing
from intel_projection import assert_no_leak


def newest_save(root: Path) -> Path | None:
    """Newest save under root.

    Accepts packed `.sav` archives (what Stellaris actually writes) and bare
    uncompressed `gamestate` files (how they get shared for debugging).
    """
    saves = [p for p in root.rglob("*.sav") if p.is_file()]
    saves += [p for p in root.rglob("*gamestate*") if p.is_file()]
    return max(saves, key=lambda p: p.stat().st_mtime) if saves else None


def unmet_empire_names(save_path: str, met_handles: set[int], player: int) -> list[str]:
    """Names of empires the player has never contacted.

    Read from the omniscient country table on purpose: this list exists solely
    so the extension can detect a leak. It is written to audit.json and is
    never injected into a prompt.
    """
    gs = read_gamestate(save_path)
    i = gs.index("\ncountry=\n")
    s, e = _matching_block(gs, i)
    countries = gs[s:e]

    met_names: set[str] = set()
    unmet_names: set[str] = set()

    for m in re.finditer(r"\n\t(\d+)=\n", countries):
        cid = int(m.group(1))
        bs, be = _matching_block(countries, m.end())
        blk = countries[bs:be]
        nm = blk.find("\n\t\tname=")
        if nm < 0:
            continue
        ns, ne = _matching_block(blk, nm)
        name = _render_name(blk[ns:ne])
        # Short or generic names produce false positives in substring matching.
        if len(name) < 8 or name == "Unknown Empire":
            continue
        if cid in met_handles or cid == player:
            met_names.add(name)
        else:
            unmet_names.add(name)

    # Names are rendered without localisation files, so distinct empires can
    # collapse onto the same string -- including a met empire and an unmet one
    # (typically two generations of a recycled slot). An ambiguous name cannot
    # serve as a leak signal in either direction, so drop it rather than flag
    # every legitimate mention of a contact the player really does have.
    return sorted(unmet_names - met_names)


def emit(save_path: Path, out_dir: Path) -> str:
    sit, intel = build_situation(str(save_path))
    briefing = render_briefing(sit)

    unmet_names = unmet_empire_names(
        str(save_path), set(intel.country_intel), intel.player_country_id
    )

    # Last gate before anything touches disk: a leak must raise, not get
    # written. system_names is empty because system names are not yet
    # extracted into the briefing (see STATE_MAP.md), so there is nothing to
    # check on that front. country_names uses synthetic negative ids because
    # unmet_empire_names() has already resolved live handles and dropped
    # names ambiguous between a met and an unmet empire (contract 2.8); real
    # country handles are never negative, so these can't collide with a met
    # country id and cause a false positive.
    assert_no_leak(
        briefing,
        intel,
        system_names={},
        country_names={-(i + 1): name for i, name in enumerate(unmet_names)},
    )

    out_dir.mkdir(parents=True, exist_ok=True)

    # Written atomically: the extension polls this file and must never read a
    # half-written briefing.
    tmp = out_dir / "briefing.txt.tmp"
    tmp.write_text(briefing, encoding="utf-8")
    tmp.replace(out_dir / "briefing.txt")

    audit = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game_date": sit.date,
        "save": save_path.name,
        "systems_known": sit.systems_known,
        "systems_total": sit.systems_total,
        "unmet_empires": unmet_names,
    }
    tmp = out_dir / "audit.json.tmp"
    tmp.write_text(json.dumps(audit, indent=1), encoding="utf-8")
    tmp.replace(out_dir / "audit.json")

    return sit.date


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saves", required=True)
    ap.add_argument("--out", required=True,
                    help="the extension's folder inside SillyTavern")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    root = Path(os.path.expanduser(a.saves))
    out = Path(os.path.expanduser(a.out))
    if not root.exists():
        raise SystemExit(f"save directory not found: {root}")

    print(f"watching {root}\nwriting  {out}")
    last = 0.0
    while True:
        try:
            p = newest_save(root)
            if p:
                mt = p.stat().st_mtime
                if mt != last:
                    time.sleep(1.5)  # let the autosave finish writing
                    date = emit(p, out)
                    last = mt
                    print(f"[{time.strftime('%H:%M:%S')}] {p.name} -> {date}", flush=True)
        except Exception as exc:
            print(f"error: {exc}", flush=True)
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
