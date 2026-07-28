"""
extract_intel.py — pull the player's intel vectors out of a Stellaris save.

Deliberately NOT a general save parser. A full parse of a 50MB gamestate is slow
and is already solved well (stellaris-dashboard / stellaris-companion both ship
Rust parsers). This does a targeted scan for the handful of intel structures the
projection layer needs, which keeps it fast enough to run on every autosave.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from intel_projection import PlayerIntel


def _matching_block(txt: str, open_idx: int) -> tuple[int, int]:
    """Return (start, end) of the braced block whose '{' is at/after open_idx."""
    start = txt.index("{", open_idx)
    depth, i = 0, start
    while True:
        c = txt[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i
        i += 1


def _array_after(txt: str, key_pos: int) -> list[int]:
    """Read a whitespace-separated numeric array following a `key=` at key_pos."""
    start, end = _matching_block(txt, key_pos)
    return [int(float(t)) for t in txt[start + 1 : end].split()]


def read_gamestate(save_path: str | Path) -> str:
    """Read a .sav (zip) or a raw uncompressed gamestate file."""
    p = Path(save_path)
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            return z.read("gamestate").decode("utf-8", errors="replace")
    return p.read_text(encoding="utf-8", errors="replace")


def extract(gamestate: str) -> PlayerIntel:
    # --- player country id -------------------------------------------------
    m = re.search(r"\nplayer=\n\{(.*?)\n\}\n", gamestate, re.S)
    if not m:
        raise ValueError("no player block found (observer mode save?)")
    pm = re.search(r"country=(\d+)", m.group(1))
    if not pm:
        raise ValueError("player block has no country id")
    player_cid = int(pm.group(1))

    # --- locate that country's block --------------------------------------
    c_idx = gamestate.index("\ncountry=\n")
    c_start, c_end = _matching_block(gamestate, c_idx)
    countries = gamestate[c_start:c_end]

    km = re.search(r"\n\t%d=\n" % player_cid, countries)
    if not km:
        raise ValueError(f"player country {player_cid} not found in country block")
    b_start, b_end = _matching_block(countries, km.end())
    block = countries[b_start:b_end]

    # --- per-system intel vectors -----------------------------------------
    def vector(key: str) -> list[int]:
        vm = re.search(r"\n\t\t%s=\n" % key, block)
        if not vm:
            return []
        return _array_after(block, vm.end())

    system_intel = vector("intel_level")
    system_peak = vector("highest_intel_level")

    # --- per-empire intel --------------------------------------------------
    # Live country handles, used to discard stale-generation entries below.
    live_countries = {int(x) for x in re.findall(r"\n\t(\d+)=", countries)}

    country_intel: dict[int, float] = {}
    country_stale: dict[int, dict] = {}
    defunct: dict[int, float] = {}

    im = block.find("\n\t\tintel_manager=")
    if im >= 0:
        m_start, m_end = _matching_block(block, im)
        inner = block[m_start:m_end]
        i_key = inner.find("intel=")
        i_start, i_end = _matching_block(inner, i_key)
        body = inner[i_start:i_end]

        # Entries are `{ \n <handle> \n { intel=N stale_intel={...} } }`.
        # Brace-matched, not regex-scanned: the nested layout defeats lazy regex.
        for em in re.finditer(r"\{\s*\n\s*(\d+)\s*\n", body):
            handle = int(em.group(1))
            try:
                r_start, r_end = _matching_block(body, em.end())
            except (ValueError, IndexError):
                continue
            rec = body[r_start:r_end]

            sm = re.search(r"intel=([0-9.]+)", rec)
            score = float(sm.group(1)) if sm else 0.0

            if handle not in live_countries:
                # Same slot, older generation: an empire that no longer exists.
                # Keep for history, never treat as a met contact.
                defunct[handle] = score
                continue

            country_intel[handle] = score
            believed = {}
            for metric in ("relative_economy", "intel_tech_relative_power", "relative_fleet"):
                rm = re.search(metric + r"=\s*\{\s*relative_power=([0-9.eE+-]+)", rec)
                if rm:
                    believed[metric] = float(rm.group(1))
            if believed:
                country_stale[handle] = believed

    pi = PlayerIntel(
        player_country_id=player_cid,
        system_intel=system_intel,
        system_intel_peak=system_peak,
        country_intel=country_intel,
        country_intel_stale=country_stale,
    )
    pi.defunct_contacts = defunct
    return pi


def extract_from_save(save_path: str | Path) -> PlayerIntel:
    return extract(read_gamestate(save_path))
