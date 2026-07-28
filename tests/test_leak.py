"""Leak-detector wiring tests.

assert_no_leak() is the last gate before a briefing reaches disk, but it was
never called from anywhere -- REDACTION_CONTRACT.md 3 requires it to run in CI
and fail the build, and previously it just sat there, defined and unused.
These tests pin two things: that it actually fires inside watcher.emit()
(not just that the function exists), and that legitimate briefings from the
example saves don't trip it.

Run: python tests/test_leak.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import game_state                                                   # noqa: E402
import watcher                                                      # noqa: E402
from intel_projection import IntelLeak, PlayerIntel, assert_no_leak  # noqa: E402

SAVES = sorted((Path(__file__).resolve().parents[1] / "Example Save Files").glob(
    "autosave_*/example_gamestate_*"))


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    return cond


def main() -> int:
    ok = True

    # 1. Unit level: assert_no_leak() itself must catch a leak and let a
    #    clean briefing through.
    intel = PlayerIntel(
        player_country_id=0,
        system_intel=[],
        system_intel_peak=[],
        country_intel={1: 90.0},  # empire 1 is met; empire 2 is not
    )
    country_names = {1: "Met Empire", 2: "Secret Hegemony"}

    try:
        assert_no_leak("nothing interesting here", intel, {}, country_names)
        ok &= check("clean briefing passes", True)
    except IntelLeak:
        ok &= check("clean briefing passes", False)

    try:
        assert_no_leak("we should invade the Secret Hegemony", intel, {}, country_names)
        ok &= check("leaking briefing raises IntelLeak", False)
    except IntelLeak:
        ok &= check("leaking briefing raises IntelLeak", True)

    # 2. Wiring: watcher.emit() must call assert_no_leak() before writing
    #    files. Force render_briefing() to append a real unmet empire's name
    #    and confirm emit() raises and writes nothing. This is the check that
    #    catches "the function exists but nobody calls it" -- it fails
    #    against the pre-fix code, which wrote the briefing regardless.
    for p in SAVES:
        _, intel = game_state.build_situation(str(p))
        unmet = watcher.unmet_empire_names(
            str(p), set(intel.country_intel), intel.player_country_id
        )
        ok &= check(f"{p.parent.name}: has an unmet empire to force a leak with", bool(unmet))
        if not unmet:
            continue

        real_render = watcher.render_briefing
        watcher.render_briefing = lambda s, _name=unmet[0]: real_render(s) + f"\n{_name}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                try:
                    watcher.emit(p, out)
                    ok &= check(f"{p.parent.name}: forced leak raises IntelLeak", False)
                except IntelLeak:
                    ok &= check(f"{p.parent.name}: forced leak raises IntelLeak", True)
                ok &= check(
                    f"{p.parent.name}: forced leak writes no files",
                    not (out / "briefing.txt").exists() and not (out / "audit.json").exists(),
                )
        finally:
            watcher.render_briefing = real_render

    # 3. Real saves: legitimate briefings must not trip the detector, and
    #    emit() must still write both files when nothing leaks.
    for p in SAVES:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            try:
                watcher.emit(p, out)
                ok &= check(f"{p.parent.name}: real briefing passes the leak check", True)
            except IntelLeak as exc:
                ok &= check(f"{p.parent.name}: real briefing passes the leak check ({exc})", False)
            ok &= check(
                f"{p.parent.name}: files were written",
                (out / "briefing.txt").exists() and (out / "audit.json").exists(),
            )

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
