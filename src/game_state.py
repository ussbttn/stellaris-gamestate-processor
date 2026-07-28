"""
game_state.py — extract the strategically relevant state, already redacted.

Everything here goes through PlayerIntel. Nothing reads omniscient data except
to immediately discard it.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

from extract_intel import _matching_block, extract, read_gamestate
from intel_projection import IntelLevel, PlayerIntel, WarView, project_wars

RESOURCES = (
    "energy", "minerals", "food", "alloys", "consumer_goods", "influence",
    "unity", "physics_research", "society_research", "engineering_research",
    "volatile_motes", "exotic_gases", "rare_crystals",
)


def _block(text: str, key: str, depth: int) -> str | None:
    marker = "\n" + "\t" * depth + key + "="
    i = text.find(marker)
    if i < 0:
        return None
    s, e = _matching_block(text, i)
    return text[s:e]


def _sum_resources(scope: str) -> dict[str, float]:
    """Sum a budget scope (income/expenses/balance) across all its sources."""
    totals: dict[str, float] = {}
    for res in RESOURCES:
        for m in re.finditer(r"\n\t+%s=([-0-9.]+)" % res, scope):
            totals[res] = totals.get(res, 0.0) + float(m.group(1))
    return totals


def _render_name(name_block: str) -> str:
    """Best-effort empire name.

    Proper rendering needs the game's localisation files; this pulls the
    literal and species fragments, which is usually recognisable.
    """
    parts = []
    for k in re.findall(r'key="([^"]+)"', name_block):
        if k.startswith("%") or k in ("adjective", "1"):
            continue
        frag = k.replace("SPEC_", "").replace("_", " ").strip()
        # Localisation keys leave literal NAME/Name tokens behind.
        frag = re.sub(r"\b(NAME|Name)\b", "", frag).strip()
        if frag:
            parts.append(frag)
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return " ".join(out[:3]) or "Unknown Empire"


def _date_parts(d: str | None) -> tuple[int, int, int] | None:
    if not d:
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", d.strip().strip('"'))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _elapsed(start: str | None, now: str | None) -> str:
    """Human duration between two Stellaris dates (YYYY.MM.DD)."""
    a, b = _date_parts(start), _date_parts(now)
    if not a or not b:
        return "unknown duration"
    months = (b[0] - a[0]) * 12 + (b[1] - a[1])
    if b[2] < a[2]:
        months -= 1
    if months < 0:
        return "unknown duration"
    y, mo = divmod(months, 12)
    bits = []
    if y:
        bits.append(f"{y} year" + ("s" if y != 1 else ""))
    if mo:
        bits.append(f"{mo} month" + ("s" if mo != 1 else ""))
    return ", ".join(bits) or "under a month"


def _war_goal(block: str | None) -> str | None:
    """Render a war goal from its type token.

    Localisation files are not available (see README), so the token is
    de-prefixed rather than looked up: `wg_independence` -> "independence".
    """
    if not block:
        return None
    m = re.search(r'type="([^"]+)"', block)
    if not m:
        return None
    return re.sub(r"^wg_", "", m.group(1)).replace("_", " ")


def _parse_wars(gs: str) -> list[dict]:
    """Parse the top-level `war` block into raw, still-omniscient records.

    Nothing is filtered here -- this is the omniscient read, and every record it
    returns goes through project_wars() before it can reach the briefing.

    Cleared slots are written `<id>=none` rather than removed, so entries
    without a block are skipped.
    """
    try:
        i = gs.index("\nwar=\n")
    except ValueError:
        return []
    ws, we = _matching_block(gs, i)
    block = gs[ws : we + 1]

    wars: list[dict] = []
    for m in re.finditer(r"\n\t(\d+)=(\n|none)", block):
        if m.group(2) == "none":
            continue
        try:
            bs, be = _matching_block(block, m.end())
        except (ValueError, IndexError):
            continue
        rec = block[bs:be]

        def side(key: str) -> list[tuple[int, str]]:
            sub = _block(rec, key, 2)
            if not sub:
                return []
            out = []
            for em in re.finditer(
                r"call_type=(\w+)\s*\n\s*country=(\d+)", sub
            ):
                out.append((int(em.group(2)), em.group(1)))
            return out

        def exhaustion(key: str) -> float | None:
            em = re.search(r"\n\t\t%s=([0-9.]+)" % key, rec)
            return float(em.group(1)) if em else None

        sd = re.search(r'\n\t\tstart_date=\s*"?([0-9.]+)', rec)
        wars.append(
            {
                "war_id": int(m.group(1)),
                "start_date": sd.group(1) if sd else None,
                "attackers": side("attackers"),
                "defenders": side("defenders"),
                "attacker_goal": _war_goal(_block(rec, "attacker_war_goal", 2)),
                "defender_goal": _war_goal(_block(rec, "defender_war_goal", 2)),
                "attacker_exhaustion": exhaustion("attacker_war_exhaustion"),
                "defender_exhaustion": exhaustion("defender_war_exhaustion"),
            }
        )
    return wars


@dataclass
class EmpireView:
    handle: int
    name: str
    intel_score: float
    believed: dict = field(default_factory=dict)


@dataclass
class Situation:
    date: str
    version: str
    empire_name: str
    stockpiles: dict[str, float]
    net_income: dict[str, float]
    military_power: float
    used_naval_capacity: float
    owned_planets: int
    known_empires: list[EmpireView]
    systems_known: int
    systems_total: int
    systems_stale: int
    wars: list[WarView]


def build_situation(save_path: str) -> tuple[Situation, PlayerIntel]:
    gs = read_gamestate(save_path)
    intel = extract(gs)

    date = re.search(r'\ndate="([^"]+)"', gs)
    # Prefer meta (present in a packed .sav); fall back to the gamestate's own
    # first line, which is how unpacked saves carry it.
    version = None
    try:
        import zipfile
        if zipfile.is_zipfile(save_path):
            with zipfile.ZipFile(save_path) as z:
                version = re.search(r'version="([^"]+)"',
                                    z.read("meta").decode("utf-8", "replace"))
    except Exception:
        pass
    if not version:
        version = re.search(r'^version="([^"]+)"', gs, re.M)

    i = gs.index("\ncountry=\n")
    cs, ce = _matching_block(gs, i)
    countries = gs[cs:ce]

    km = re.search(r"\n\t%d=\n" % intel.player_country_id, countries)
    bs, be = _matching_block(countries, km.end())
    me = countries[bs:be]

    # --- economy ---------------------------------------------------------
    stock: dict[str, float] = {}
    econ = _block(me, "standard_economy_module", 3)
    if econ:
        for res in RESOURCES:
            m = re.search(r"\n\t+%s=([-0-9.]+)" % res, econ)
            if m:
                stock[res] = float(m.group(1))

    net: dict[str, float] = {}
    budget = _block(me, "budget", 2)
    if budget:
        last = _block(budget, "last_month", 3) or _block(budget, "current_month", 3)
        if last:
            bal = _block(last, "balance", 4)
            if bal:
                net = _sum_resources(bal)

    def scalar(key: str, default: float = 0.0) -> float:
        m = re.search(r"\n\t\t%s=([-0-9.]+)" % key, me)
        return float(m.group(1)) if m else default

    nameblk = _block(me, "name", 2) or ""

    # --- known empires (redaction happens here) --------------------------
    known: list[EmpireView] = []
    for handle, score in sorted(intel.country_intel.items(), key=lambda kv: -kv[1]):
        if handle == intel.player_country_id:
            continue
        m = re.search(r"\n\t%d=\n" % handle, countries)
        if not m:
            continue
        obs, obe = _matching_block(countries, m.end())
        other = countries[obs:obe]
        known.append(
            EmpireView(
                handle=handle,
                name=_render_name(_block(other, "name", 2) or ""),
                intel_score=score,
                believed=intel.country_intel_stale.get(handle, {}),
            )
        )

    owned = _block(me, "owned_planets", 2) or ""
    planet_count = len(owned.split())

    # --- wars (redaction happens in project_wars) ------------------------
    # Names come from the LIVE country table only. A war handle absent from
    # this map is a recycled slot at a lower generation -- a destroyed empire --
    # and project_wars drops its war rather than resolving it to the live
    # occupant of the same slot.
    live_names: dict[int, str] = {}
    for m in re.finditer(r"\n\t(\d+)=\n", countries):
        cid = int(m.group(1))
        bs2, be2 = _matching_block(countries, m.end())
        live_names[cid] = _render_name(_block(countries[bs2:be2], "name", 2) or "")

    now = date.group(1) if date else None
    wars = project_wars(
        _parse_wars(gs), intel, live_names, lambda s: _elapsed(s, now)
    )

    return (
        Situation(
            date=date.group(1) if date else "?",
            version=version.group(1) if version else "?",
            empire_name=_render_name(nameblk),
            stockpiles=stock,
            net_income=net,
            military_power=scalar("military_power"),
            used_naval_capacity=scalar("used_naval_capacity"),
            owned_planets=planet_count,
            known_empires=known,
            systems_known=len(intel.known_systems()),
            systems_total=len(intel.system_intel),
            systems_stale=len(intel.remembered_systems()),
            wars=wars,
        ),
        intel,
    )


_warned_high_exhaustion = False


def _exhaustion_pct(v: float | None) -> str:
    """Render war exhaustion as a percentage.

    REDACTION_CONTRACT.md 2 flags the 0-1 scale as ASSUMED: every observed
    sample is exactly 1, saturated alongside force_peace=yes, so a 0-100 save
    would look identical so far. Treat <=1 as a fraction (the assumed case)
    and >1 as already a percentage, so a save that turns out to use 0-100
    doesn't silently render as single-digit percentages.
    """
    global _warned_high_exhaustion
    if v is None:
        return "unknown"
    if v > 1:
        if not _warned_high_exhaustion:
            print(
                f"warning: war exhaustion value {v!r} > 1 -- treating the scale as "
                "already 0-100, not the assumed 0-1 (REDACTION_CONTRACT.md 2)",
                file=sys.stderr,
            )
            _warned_high_exhaustion = True
        return f"{v:.0f}%"
    return f"{v * 100:.0f}%"


def render_briefing(s: Situation) -> str:
    """The text injected into the model's context. Redacted by construction."""
    L = [
        f"# SITUATION REPORT — {s.date}",
        f"Empire: {s.empire_name}   |   Stellaris {s.version}",
        "",
        "## Known space",
        f"You have current intel on {s.systems_known} of {s.systems_total} systems "
        f"({s.systems_known / max(s.systems_total,1):.0%}). "
        f"{s.systems_stale} further systems are known only from dated reports.",
        "",
        "## Economy (monthly net)",
    ]
    for res in RESOURCES:
        if res in s.stockpiles or res in s.net_income:
            stock = s.stockpiles.get(res, 0.0)
            flow = s.net_income.get(res, 0.0)
            flag = "  <-- DEFICIT" if flow < 0 else ""
            L.append(f"  {res:22s} {stock:12,.0f}  ({flow:+.1f}/mo){flag}")

    L += [
        "",
        "## Military",
        f"  fleet power          {s.military_power:,.0f}",
        f"  naval capacity used  {s.used_naval_capacity:,.0f}",
        f"  colonies             {s.owned_planets}",
        "",
        "## Active wars",
    ]

    if not s.wars:
        # Say this positively. An empty heading invites the model to fill the
        # gap; stating peace explicitly does not.
        L.append("  None. Your empire is not at war, and no war is known to be "
                 "under way between empires you have contact with.")
    for w in s.wars:
        att = " + ".join(b.name for b in w.attackers)
        dfn = " + ".join(b.name for b in w.defenders)
        tag = "  [YOU ARE A BELLIGERENT]" if w.player_is_party else ""
        L.append(f"  {att}  vs.  {dfn}{tag}")
        goals = []
        if w.attacker_goal:
            goals.append(f"attacker goal: {w.attacker_goal}")
        if w.defender_goal:
            goals.append(f"defender goal: {w.defender_goal}")
        if goals:
            L.append("      " + "; ".join(goals))
        if w.attacker_exhaustion is not None or w.defender_exhaustion is not None:
            L.append(
                f"      war exhaustion — attacker {_exhaustion_pct(w.attacker_exhaustion)}, "
                f"defender {_exhaustion_pct(w.defender_exhaustion)}"
            )
        else:
            L.append("      war exhaustion not known at your level of intel")
        L.append(f"      running {w.duration} (since {w.start_date})")

    shown = s.known_empires[:15]
    L += [
        "",
        f"## Known empires ({len(s.known_empires)} contacts, {len(shown)} shown, "
        "highest intel first)",
    ]
    for e in shown:
        line = f"  {e.name:28s} intel {e.intel_score:5.1f}"
        if e.believed:
            rf = e.believed.get("relative_fleet")
            if rf:
                line += f"  believed fleet ratio {rf:.2f}"
        L.append(line)

    L += [
        "",
        "IMPORTANT: this report is the *entirety* of what the commander knows. "
        "Systems and empires not listed are genuinely unknown to them. Do not "
        "speculate about specific unlisted systems, empires, or fleets as though "
        "they were observed facts.",
    ]
    return "\n".join(L)
