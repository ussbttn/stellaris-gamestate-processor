"""
intel_projection.py — fog-of-war projection for Stellaris save data.

The save file is omniscient. This module is the choke point that turns omniscient
state into *what the player is entitled to know*, so that privileged data never
reaches an LLM context window.

Design rule: REDACT BY CONSTRUCTION, NEVER BY INSTRUCTION.
Nothing downstream is trusted to "ignore" data it can see.

Save schema notes (validated against Phoenix v4.0.1, save year 2312):

  country[<cid>].intel_level         = flat array, INDEXED BY SYSTEM ID, values 0-4
  country[<cid>].highest_intel_level = same shape; peak intel ever held per system
  country[<cid>].intel_manager.intel = list of (target_country_id, {intel: 0-100,
                                       stale_intel: {relative_economy,
                                       intel_tech_relative_power, relative_fleet}})
  country[<cid>].visited_objects     = systems ever visited (NOT parallel to the
                                       intel arrays -- different length, do not zip)
  player[0].country                  = player's country id (a type-tagged handle,
                                       e.g. 16777223; treat as opaque, never as an index)

Country ids are type-tagged 32-bit handles (high byte = object type, low 24 bits =
index). They are NOT sequential and must be used as opaque keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable


class IntelLevel(IntEnum):
    """Per-system intel, as stored in the save (0-4).

    NOTE: the numeric scale is confirmed from save data; the human labels below are
    provisional and should be pinned against the in-game intel UI before shipping
    anything user-facing that names them.
    """

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FULL = 4


# What a player may learn about a system at each intel level.
#
# This table IS the game-fairness contract. It is deliberately conservative: when
# unsure whether the player can see a field in-game, omit it here. Widening the
# table later is safe; narrowing it after the model has already leaked is not.
VISIBILITY: dict[IntelLevel, frozenset[str]] = {
    IntelLevel.NONE: frozenset(),
    IntelLevel.LOW: frozenset({
        "system_id", "name", "coordinate", "hyperlanes", "owner",
    }),
    IntelLevel.MEDIUM: frozenset({
        "system_id", "name", "coordinate", "hyperlanes", "owner",
        "starbase_present", "planet_count", "megastructure_present",
    }),
    IntelLevel.HIGH: frozenset({
        "system_id", "name", "coordinate", "hyperlanes", "owner",
        "starbase_present", "planet_count", "megastructure_present",
        "starbase_level", "planets", "deposits", "fleet_presence",
    }),
    IntelLevel.FULL: frozenset({
        "system_id", "name", "coordinate", "hyperlanes", "owner",
        "starbase_present", "planet_count", "megastructure_present",
        "starbase_level", "planets", "deposits", "fleet_presence",
        "fleet_composition", "defensive_modules", "army_presence",
    }),
}


@dataclass(frozen=True)
class SystemView:
    """A system as the player is entitled to perceive it."""

    system_id: int
    level: IntelLevel
    fields: dict[str, Any]
    # True when the player once knew more than they know now. The payload is
    # remembered, not current -- downstream must present it as dated.
    is_stale: bool = False
    peak_level: IntelLevel = IntelLevel.NONE

    @property
    def is_known(self) -> bool:
        return self.level > IntelLevel.NONE or self.is_stale


@dataclass
class PlayerIntel:
    """The player's epistemic position, extracted from one save."""

    player_country_id: int
    system_intel: list[int]
    system_intel_peak: list[int]
    country_intel: dict[int, float] = field(default_factory=dict)
    country_intel_stale: dict[int, dict] = field(default_factory=dict)
    # Handles for empires that no longer exist (slot reused at a higher
    # generation). Historical record only -- never a live contact.
    defunct_contacts: dict[int, float] = field(default_factory=dict)

    def level_for(self, system_id: int) -> IntelLevel:
        if 0 <= system_id < len(self.system_intel):
            return IntelLevel(self.system_intel[system_id])
        return IntelLevel.NONE

    def peak_for(self, system_id: int) -> IntelLevel:
        if 0 <= system_id < len(self.system_intel_peak):
            return IntelLevel(self.system_intel_peak[system_id])
        return IntelLevel.NONE

    def known_systems(self) -> list[int]:
        """Systems the player currently has any intel on."""
        return [i for i, lv in enumerate(self.system_intel) if lv > 0]

    def remembered_systems(self) -> list[int]:
        """Systems the player once knew better than they do now."""
        return [
            i
            for i, (cur, peak) in enumerate(zip(self.system_intel, self.system_intel_peak))
            if peak > cur
        ]

    def met_countries(self) -> set[int]:
        """Countries the player has established contact with."""
        return set(self.country_intel)


class Redactor:
    """Projects omniscient galaxy state onto the player's intel."""

    def __init__(self, intel: PlayerIntel, *, allow_stale: bool = True):
        self.intel = intel
        self.allow_stale = allow_stale

    def project_system(self, system_id: int, truth: dict[str, Any]) -> SystemView | None:
        """Return only the fields the player may see. None if wholly unknown.

        `truth` is the omniscient system record. Fields absent from the
        visibility set for the player's intel level are dropped here and never
        travel further.
        """
        level = self.intel.level_for(system_id)
        peak = self.intel.peak_for(system_id)

        effective, stale = level, False
        if level == IntelLevel.NONE and peak > IntelLevel.NONE and self.allow_stale:
            # Player has forgotten this system but retains dated knowledge.
            # Grant the remembered tier, flagged so callers must date it.
            effective, stale = peak, True

        if effective == IntelLevel.NONE:
            return None

        allowed = VISIBILITY[effective]
        return SystemView(
            system_id=system_id,
            level=level,
            fields={k: v for k, v in truth.items() if k in allowed},
            is_stale=stale,
            peak_level=peak,
        )

    def project_galaxy(
        self, truth_by_system: dict[int, dict[str, Any]]
    ) -> dict[int, SystemView]:
        out = {}
        for sid, truth in truth_by_system.items():
            view = self.project_system(sid, truth)
            if view is not None:
                out[sid] = view
        return out

    def project_countries(self, truth_by_country: dict[int, dict]) -> dict[int, dict]:
        """Empire-level redaction.

        Unmet empires are dropped entirely -- the player should not know they
        exist. For met empires, the save's own `stale_intel` relative-power
        figures are the honest source: they are what the player's intelligence
        services believe, not ground truth.
        """
        out = {}
        for cid, truth in truth_by_country.items():
            if cid == self.intel.player_country_id:
                out[cid] = dict(truth)  # full self-knowledge
                continue
            if cid not in self.intel.country_intel:
                continue  # never met: does not exist as far as the player knows
            score = self.intel.country_intel[cid]
            rec = {"country_id": cid, "intel_score": score}
            for k in ("name", "flag", "government_type"):
                if k in truth:
                    rec[k] = truth[k]
            # Relative-power estimates rather than true values.
            believed = self.intel.country_intel_stale.get(cid)
            if believed:
                rec["believed_relative_power"] = believed
            if score >= 30:
                for k in ("ethics", "capital_system"):
                    if k in truth:
                        rec[k] = truth[k]
            if score >= 60:
                for k in ("fleet_power", "tech_count", "resource_income"):
                    if k in truth:
                        rec[k] = truth[k]
            out[cid] = rec
        return out


# --------------------------------------------------------------------------
# Leak detection -- run this in CI on every briefing before it reaches a model.
# --------------------------------------------------------------------------

class IntelLeak(AssertionError):
    pass


def assert_no_leak(
    briefing: str,
    intel: PlayerIntel,
    system_names: dict[int, str],
    country_names: dict[int, str],
) -> None:
    """Fail loudly if a rendered briefing names anything the player can't know.

    Cheap, blunt, and effective: substring matching against the names of
    unknown systems and unmet empires. A model that has been fed privileged
    data usually gives itself away by naming it.
    """
    leaks: list[str] = []

    for sid, name in system_names.items():
        if not name or len(name) < 4:
            continue  # too short to match reliably
        if intel.level_for(sid) == IntelLevel.NONE and intel.peak_for(sid) == IntelLevel.NONE:
            if name in briefing:
                leaks.append(f"unknown system {sid!r} ({name!r})")

    met = intel.met_countries() | {intel.player_country_id}
    for cid, name in country_names.items():
        if cid in met or not name or len(name) < 4:
            continue
        if name in briefing:
            leaks.append(f"unmet empire {cid!r} ({name!r})")

    if leaks:
        raise IntelLeak(
            f"briefing leaks {len(leaks)} privileged item(s): " + "; ".join(leaks[:10])
        )
