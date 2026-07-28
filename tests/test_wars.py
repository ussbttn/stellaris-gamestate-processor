"""War gate tests.

The example saves contain no war the player may see, so the briefing's war
section is empty for all three. That is the correct result, but an empty section
is also what a broken parser produces, so these tests pin the difference:
the gate is exercised against synthetic records where the answer is known.

Run: python tests/test_wars.py
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import game_state                                            # noqa: E402
from extract_intel import extract, read_gamestate           # noqa: E402
from game_state import (                                    # noqa: E402
    _elapsed, _exhaustion_pct, _parse_wars, build_situation, render_briefing,
)
from intel_projection import PlayerIntel, project_wars      # noqa: E402

SAVES = sorted((Path(__file__).resolve().parents[1] / "Example Save Files").glob(
    "autosave_*/example_gamestate_*"))

PLAYER = 0
MET_HIGH = (1 << 24) | 51        # live, met, intel 90
MET_LOW = (2 << 24) | 12         # live, met, intel 20
UNMET = (3 << 24) | 33           # live, never contacted
DEAD = (5 << 24) | 8             # destroyed; slot 8 now lives at generation 7
HEIR = (7 << 24) | 8             # the living empire that inherited slot 8

NAMES = {
    PLAYER: "Player Imperium",
    MET_HIGH: "Highly Scouted Union",
    MET_LOW: "Barely Known Combine",
    UNMET: "Never Contacted Hegemony",
    HEIR: "Slot Eight Successor",
    # DEAD is deliberately absent: it is not a live country.
}

INTEL = PlayerIntel(
    player_country_id=PLAYER,
    system_intel=[],
    system_intel_peak=[],
    country_intel={MET_HIGH: 90.0, MET_LOW: 20.0, HEIR: 75.0},
)


def war(wid, att, dfn, **kw):
    return {
        "war_id": wid,
        "start_date": "2385.06.01",
        "attackers": [(att, "primary")],
        "defenders": [(dfn, "primary")],
        "attacker_goal": "independence",
        "defender_goal": "assert overlordship",
        "attacker_exhaustion": 0.42,
        "defender_exhaustion": 0.13,
        **kw,
    }


def project(wars):
    return project_wars(wars, INTEL, NAMES, lambda s: _elapsed(s, "2398.01.01"))


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    return cond


def main() -> int:
    ok = True

    # 1. The player's own war renders in full.
    v = project([war(1, PLAYER, MET_HIGH)])
    ok &= check("own war is kept", len(v) == 1)
    ok &= check("own war is flagged as the player's", v[0].player_is_party)
    ok &= check("own war shows both exhaustions",
                v[0].attacker_exhaustion == 0.42 and v[0].defender_exhaustion == 0.13)
    ok &= check("duration is computed", v[0].duration == "12 years, 7 months")

    # 2. Third-party war between two well-scouted contacts: kept, with numbers.
    v = project([war(2, MET_HIGH, HEIR)])
    ok &= check("third-party war between met empires is kept", len(v) == 1)
    ok &= check("third-party war is not flagged as the player's",
                not v[0].player_is_party)
    ok &= check("well-scouted third-party war keeps exhaustion",
                v[0].attacker_exhaustion == 0.42)

    # 3. Same war, one party barely known: kept, numbers withheld.
    v = project([war(3, MET_HIGH, MET_LOW)])
    ok &= check("poorly-scouted third-party war is still kept", len(v) == 1)
    ok &= check("poorly-scouted third-party war withholds exhaustion",
                v[0].attacker_exhaustion is None and v[0].defender_exhaustion is None)

    # 4. A war with an unmet primary is dropped WHOLE -- naming only the met
    #    side would still disclose that the unmet empire exists.
    v = project([war(4, MET_HIGH, UNMET)])
    ok &= check("war with an unmet belligerent is dropped", v == [])

    # 5. Contract 3 test 5: a lower-generation handle must never surface as a
    #    live contact. The trap is that DEAD and HEIR share slot 8, so a gate
    #    matching on slot would render this war as involving HEIR.
    v = project([war(5, MET_HIGH, DEAD)])
    ok &= check("war with a destroyed belligerent is dropped", v == [])
    rendered = " ".join(b.name for w in project([war(5, MET_HIGH, DEAD)])
                        for b in w.attackers + w.defenders)
    ok &= check("destroyed handle does not resolve to its slot's heir",
                "Slot Eight Successor" not in rendered)

    # 6. Ordering: the player's own war comes first.
    v = project([war(6, MET_HIGH, HEIR), war(7, PLAYER, MET_HIGH)])
    ok &= check("player's own war sorts first",
                len(v) == 2 and v[0].player_is_party)

    # 7. End-to-end render against a synthetic situation.
    sit, _ = build_situation(str(SAVES[-1]))
    sit.wars = project([war(8, PLAYER, MET_HIGH), war(9, MET_HIGH, MET_LOW)])
    text = render_briefing(sit)
    ok &= check("briefing contains the war section", "## Active wars" in text)
    ok &= check("briefing names both belligerents",
                "Player Imperium  vs.  Highly Scouted Union" in text)
    ok &= check("briefing marks the player's war",
                "[YOU ARE A BELLIGERENT]" in text)
    ok &= check("briefing renders exhaustion as a percentage",
                "attacker 42%, defender 13%" in text)
    ok &= check("briefing states when exhaustion is unknown",
                "war exhaustion not known at your level of intel" in text)
    print("\n--- synthetic war section ---")
    start = text.index("## Active wars")
    print(text[start:text.index("## Known empires", start)].rstrip())

    # 8. The real saves: parser finds the records, gate admits none of them.
    print("\n--- example saves ---")
    for p in SAVES:
        gs = read_gamestate(str(p))
        raw = _parse_wars(gs)
        kept = project_wars(_parse_wars(gs), extract(gs), {}, lambda s: "")
        ok &= check(f"{p.parent.name}: parser found 2 live war records", len(raw) == 2)
        ok &= check(f"{p.parent.name}: gate admits none", kept == [])

    # 9. Exhaustion scale is unconfirmed (0-1 vs 0-100 -- REDACTION_CONTRACT.md
    #    2), so the renderer must guess defensively rather than assume 0-1.
    print("\n--- exhaustion scale defensiveness ---")
    game_state._warned_high_exhaustion = False  # isolate from any prior call
    ok &= check("fractional exhaustion renders as a percentage",
                _exhaustion_pct(0.42) == "42%")
    ok &= check("saturated 0-1 exhaustion renders as 100%",
                _exhaustion_pct(1) == "100%")
    ok &= check("unknown exhaustion renders as 'unknown'",
                _exhaustion_pct(None) == "unknown")

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rendered = _exhaustion_pct(84)
    ok &= check("a value already >1 is treated as a percentage, not re-scaled",
                rendered == "84%")
    ok &= check("the first over-1 value logs a warning", "warning" in buf.getvalue().lower())

    buf2 = io.StringIO()
    with contextlib.redirect_stderr(buf2):
        _exhaustion_pct(90)
    ok &= check("the warning fires only once", buf2.getvalue() == "")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
