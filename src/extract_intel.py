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


# Flags confirmed as literal `relation=` fields against the example saves
# (relations_manager.relation, always owner=<the block's own country>).
# `subject=yes` on a relation means the OTHER party (`country=`) is that
# owner's subject -- so inside the player's own relations_manager it marks
# the player as overlord. Detecting "player is a subject of X" would need
# the reverse (X's own relations_manager), which is not read here.
_RELATION_PACT_FLAGS = ("commercial_pact", "research_agreement", "embassy", "defensive_pact")


def _relation_pacts(country_block: str) -> dict[int, set[str]]:
    """Per-country active pacts from the player's own relations_manager."""
    rmi = country_block.find("\n\t\trelations_manager=")
    if rmi < 0:
        return {}
    rs, re_ = _matching_block(country_block, rmi)
    rm = country_block[rs:re_]

    out: dict[int, set[str]] = {}
    for m in re.finditer(r"\n\t\t\trelation=\n", rm):
        try:
            bs, be = _matching_block(rm, m.end())
        except (ValueError, IndexError):
            continue
        rec = rm[bs:be]
        cm = re.search(r"\n\t{4}country=(\d+)", rec)
        if not cm:
            continue
        cid = int(cm.group(1))
        pacts = {flag for flag in _RELATION_PACT_FLAGS if f"\n\t\t\t\t{flag}=yes" in rec}
        if "\n\t\t\t\tsubject=yes" in rec:
            pacts.add("overlord")
        if pacts:
            out[cid] = pacts
    return out


def _federation_pacts(gamestate: str, player_cid: int) -> dict[int, set[str]]:
    """Fellow federation members' pact tokens, from the top-level federation= block.

    Any member gets `federation_member` (default/hegemony/spiritualist grant:
    waystations). The federation_type string additionally names its
    specialisation -- observed literal values include "hegemony_federation"
    and "research_federation".
    """
    try:
        i = gamestate.index("\nfederation=\n")
    except ValueError:
        return {}
    s, e = _matching_block(gamestate, i)
    block = gamestate[s:e]

    out: dict[int, set[str]] = {}
    for m in re.finditer(r"\n\t(\d+)=\n", block):
        try:
            bs, be = _matching_block(block, m.end())
        except (ValueError, IndexError):
            continue
        rec = block[bs:be]
        mm = re.search(r"\n\t\tmembers=\n", rec)
        if not mm:
            continue
        members = set(_array_after(rec, mm.end()))
        if player_cid not in members:
            continue
        ftm = re.search(r'federation_type="([^"]+)"', rec)
        fed_type = ftm.group(1) if ftm else ""
        tokens = {"federation_member"}
        if "military" in fed_type:
            tokens.add("military_federation")
        if "research" in fed_type:
            tokens.add("research_federation")
        if "trade" in fed_type or "commercial" in fed_type:
            tokens.add("trade_federation")
        for other in members:
            if other != player_cid:
                out.setdefault(other, set()).update(tokens)
    return out


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

    # --- active pacts, for pact-derived intel (REDACTION_CONTRACT.md 1.2a) --
    pacts: dict[int, set[str]] = {}
    for cid, tokens in _relation_pacts(block).items():
        pacts.setdefault(cid, set()).update(tokens)
    for cid, tokens in _federation_pacts(gamestate, player_cid).items():
        pacts.setdefault(cid, set()).update(tokens)

    pi = PlayerIntel(
        player_country_id=player_cid,
        system_intel=system_intel,
        system_intel_peak=system_peak,
        country_intel=country_intel,
        country_intel_stale=country_stale,
        country_pacts={cid: frozenset(tokens) for cid, tokens in pacts.items()},
    )
    pi.defunct_contacts = defunct
    return pi


def extract_from_save(save_path: str | Path) -> PlayerIntel:
    return extract(read_gamestate(save_path))
