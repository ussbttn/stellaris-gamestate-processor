"""
game_state.py — extract the strategically relevant state, already redacted.

Everything here goes through PlayerIntel. Nothing reads omniscient data except
to immediately discard it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from extract_intel import _matching_block, extract, read_gamestate
from intel_projection import IntelLevel, PlayerIntel

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
    at_war_with: list[str]


def build_situation(save_path: str) -> tuple[Situation, PlayerIntel]:
    gs = read_gamestate(save_path)
    intel = extract(gs)

    date = re.search(r'\ndate="([^"]+)"', gs)
    version = None
    try:
        import zipfile
        with zipfile.ZipFile(save_path) as z:
            version = re.search(r'version="([^"]+)"',
                                z.read("meta").decode("utf-8", "replace"))
    except Exception:
        pass

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
            at_war_with=[],
        ),
        intel,
    )


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
        f"## Known empires ({len(s.known_empires)} contacts)",
    ]
    for e in s.known_empires[:15]:
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
