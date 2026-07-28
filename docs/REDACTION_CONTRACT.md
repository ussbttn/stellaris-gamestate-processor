# Fog-of-War Redaction Contract

The specification the gamestate processor implements against. This document owns
one question: **for each piece of data, what must be true for the player to know
it?**

Verified against Pegasus v4.4.6 saves at 2396.01.01 and 2398.01.01
(Blessed Azantian Imperium, 604 systems, 83 countries).

---

## 0. The governing rule: fail closed

Every field carries a status.

| Status | Meaning | Action |
|---|---|---|
| **VERIFIED** | Gate confirmed empirically against save data | Extract, gated |
| **ASSUMED** | Gate inferred from documentation, not yet measured | Extract, gated, flagged in output |
| **UNRESOLVED** | Semantics not established | **Do not extract** |

An UNRESOLVED field is omitted entirely. Omitting data the player is entitled to
makes the advisor less useful and you will notice. Including data they are not
entitled to makes it cheat and **you will not notice**. The costs are not
symmetric, so the default is omission.

Nothing downstream — no prompt, no tool, no model instruction — is trusted to
compensate for a wrong gate here. If a field reaches the JSON, it is treated as
public.

---

## 1. Three orthogonal gates

These are separate systems answering separate questions. None substitutes for
another, and conflating them is the single most likely way to get this wrong.

### 1.1 System visibility — `intel_level` **[VERIFIED]**

```
country[P].intel_level          flat array, INDEXED BY SYSTEM ID, values 0-4
country[P].highest_intel_level  same shape, peak ever held
```

**605 values against 604 systems and 83 countries.** Confirmed in both saves.
It is *not* indexed by country — an earlier working assumption elsewhere in the
project had this wrong, and it inverts the entire gate if propagated.

`visited_objects` is a *different* length (184 in one sample). Do not zip them.

Levels: 0 none, 1 low, 2 medium, 3 high, 4 full. Label names are provisional;
the numeric scale is confirmed.

**Stale knowledge:** where `highest_intel_level[s] > intel_level[s]`, the player
retains dated information. 205 such systems at 2312, 469 at 2396. Serve the
remembered tier, marked with the level it was observed at, never as current.

### 1.2 Empire knowledge — `intel_manager` **[VERIFIED structure, ASSUMED thresholds]**

```
country[P].intel_manager.intel[<handle>]  = { intel: 0-100, stale_intel: {...} }
country[P].intel_manager.federation_intel = same shape
```

A single 0–100 scalar per empire. **The five intel categories are derived from
it, not stored** — confirmed: no category breakdown exists in the save.

Vanilla thresholds per the wiki and dev diary — Low Government at 10, Low
Military at 40, Medium Government at 40, Medium Diplomacy at 50 — meaning
*different categories unlock at different scores*. Documented reveals:

| Level | Reveals |
|---|---|
| low | government detail, relative power, rivalries, federation names, borders, casus belli |
| medium | civics, origin, governors, opinion breakdown, pacts, ship locations |
| high | sprawl, colony info, civilian ship orders, fleet details |
| full | system info, full colony info, fleet orders |

**These are ASSUMED until read from `common/`.** Mods rewrite them (Counter
Intel moves map reveals up to 50/60), so hardcoding vanilla numbers is wrong for
a modded install. Until parsed, apply the *most conservative* reading: require
the highest plausible threshold for each item.

**Not being in the map at all means not met.** An empire absent from
`intel_manager.intel` does not exist as far as the player knows — omit entirely,
including its name.

### 1.3 Survey — **[UNRESOLVED — DO NOT EXTRACT]**

Two candidate fields, neither settled:

- `country[P].surveyed_deposit_holders` — 5,410 entries. I previously read this
  as "bodies surveyed by the player, 88% coverage." **That reading is not
  defensible.** The top twelve empires cluster between 5,027 and 5,531 despite
  known-system counts ranging 428–601; personal survey effort should vary far
  more. It also exceeds the 2,809 bodies that have deposits at all, so it does
  not fit its own name.
- `planet[].surveyed_by` — a **scalar**, not a list. Records one country,
  probably the first surveyor. Using it as a visibility test is wrong in both
  directions.

**Therefore: deposits, planet features, and anomalies are not extracted for any
body outside the player's own colonies until this is settled.**

Settle it with paired saves: save, survey exactly one planet, save. Whichever
path gains exactly one entry is the survey record. `schema_diff.py diff` reports
it directly.

### 1.4 Fleet visibility **[VERIFIED]**

Two explicit lists; nothing needs recomputing from sensor ranges.

```
country[P].sensor_range_fleets                 fleet ids currently visible
country[P].systems_with_forced_visible_fleets  scripted overrides
country[P].intel[].hostile                     LAST KNOWN hostile forces
```

`sensor_range_fleets` resolved 2,597/2,597 to live fleet ids, max matching the
fleet id space. Filter `fleet[]` through the union of it and the forced-visible
systems.

`country[P].intel[].hostile` is the remembered half — name, coordinate,
`military_power`, owner, ship-class composition, for 41 systems. Serve as dated.

At 2396 this admitted 93% of fleets, because a dominant late empire sees
everything. **The filter is therefore effectively untested.** An early save is
required to confirm it excludes anything.

---

## 2. Field gates

### Own empire — no gating, extract freely

Resources and budget, technologies, traditions, ascension perks, relics, edicts,
ethics, civics, authority, origin, ruler and council, owned leaders, sectors,
own factions, own espionage operations, ship designs, naval capacity, empire
size, `timeline_events`, own `situations`, `first_contact` in progress.

### Own colonies — gate on ownership, not intel

`colony[]`, `planets.planet[]`, `districts[]`, `zones[]`, `buildings[]`,
`pop_groups[]`, `pop_jobs[]` — **filtered to planets the player owns.**

`pop_groups` needs care. A single record carries species, stratum, ethic,
faction *and* happiness together:

```
key = { species=170 category="worker" ethos={ ethic="ethic_authoritarian" }
        pop_faction=16777230 }
planet=36 size=150 happiness=0.275 power=3.9 crime=2.17
```

There is no per-observer view — it is one flat global list of 2,460 records. So
**foreign `pop_groups` are privileged in full.** Filter by planet ownership
before extraction, not after.

### Known empires — gate on `intel_manager` presence, then score

Name, government, ethics, relations, opinion, treaties, believed relative power
(`stale_intel`, not true values), federation membership, agreements, wars.

**Generation-handle filtering is mandatory.** Country ids are
`(generation << 24) | slot`. `intel_manager` retains entries for empires that no
longer exist: 141 entries against 83 live countries in the 2398 save, all extras
mapping to a live slot at a lower generation, zero orphans. Slot 40 has been
recycled 18 times.

- Match on the **exact handle**. Lower-generation handles are destroyed empires:
  historical only, never a live contact.
- **Never match on slot alone.** That merges a dead empire's intelligence into
  its living successor and presents it as current.

Naive counting reports 141 contacts, which is impossible.

### Wars — gate on every belligerent **[VERIFIED structure, ASSUMED detail gate]**

```
war[<id>]                      cleared slots are written `=none`, not removed
war[<id>].attackers[]          { call_type, country, caller, fleets_gone_mia }
war[<id>].defenders[]          same shape
war[<id>].attacker_war_goal    { type="wg_..." actor target win }
war[<id>].{attacker,defender}_war_exhaustion
war[<id>].start_date
```

A war is disclosed only when **every primary belligerent is a live, met
handle**. Naming one side and eliding the other still discloses that the other
exists, which §2 "Never extract" forbids — so the war is dropped whole. Serving
"a known empire vs. someone" is worse than silence: it reads as informative.

**This is where generation handles bite hardest.** Belligerents are stored as
raw handles, and war records outlive their participants. In all three example
saves, `war[50331651]` is fought by handle `83886088` — generation 5 of slot 8 —
while slot 8 is currently occupied by generation 7. Matching on slot would
render a destroyed empire's war as the living successor's, presented as current.
Match the exact handle; a handle absent from the live country table is not a
contact and cannot be named.

**Exhaustion is scaled 0–1, not 0–100 [ASSUMED].** Every observed value is
exactly `1`, alongside `force_peace=yes` and a `force_peace_date` — forced peace
triggers at the exhaustion cap, so `1` is the ceiling rather than one percent.
Inferred from saturated samples only; no unsaturated value has been observed.

For a war the player is fighting, both sides' exhaustion is on the in-game war
screen and is extracted. For a war between third parties it is a live readout of
another empire's military condition, so it is gated at the same score as
`fleet_power` — **ASSUMED, per §1.2's most-conservative rule**, pending
`common/`. Below that score the war still renders; only the numbers are withheld.

War names and goals use localisation keys (`NAME_WAR_OF_REVOLT`,
`wg_independence`). Goals are de-prefixed to a readable token; war names are not
rendered at all, since without `localisation/*.yml` they are less informative
than the goal.

### Known space — gate on `intel_level` per system

System name, position, hyperlanes, owner, starbase presence and level,
megastructures, bypasses, wormholes, storms, nebulae, archaeological sites,
astral rifts.

`starbase_mgr.starbases[]` has **no owner and no system field.** Resolve via
`galactic_object[sys].starbases`, then `.station` → `ships[]` → `.fleet` → owner,
where ownership itself is inverted: `country[cid].fleets_manager.owned_fleets`.
Build a fleet→owner map once per parse. Systems have no owner field either, for
the same reason.

### Public — no gating

`galactic_community` (members, council, proposed, passed), `resolution[]`,
`market` prices and access.

### Never extract

Unmet empires in any form, including existence. All `dead_*` blocks (12 of
them). Fleets outside `sensor_range_fleets`. Foreign `pop_groups`. Foreign
`spy_networks`. Deposits outside own colonies (pending §1.3).
`saved_event_target`, `flags`, `terra_incognita` raw bitmap.

---

## 3. Tests that must pass

Run on every generated pair of output files, in CI, failing the build.

1. **No unmet empire name** appears anywhere in `briefing.json` or
   `fog-of-war-gamestate.json`. Build the forbidden list from the omniscient
   country table at parse time; keep it in a **separate file** from the outputs.
2. **No system at `intel_level == 0` and `highest_intel_level == 0`** is named.
3. **No fleet absent from** `sensor_range_fleets ∪ forced_visible` appears.
4. **No `pop_group`** whose planet is not player-owned appears.
5. **No lower-generation country handle** appears as a live contact.
6. **Every stale record carries its observation level**, and no stale record is
   presented as current.
7. **No war appears unless every primary belligerent is a live, met handle**,
   and no destroyed belligerent resolves to the live occupant of its slot.
8. **Ambiguous names are dropped, not flagged.** Names render without
   localisation files, so two handles can collapse to one string — a met empire
   and an unmet one, typically two generations of one slot. "Rethellian Accord"
   did exactly this. A name that could mean either is useless as a leak signal
   and must not drive a test failure.

Test 3 is currently weak: at 2396, 93% of fleets pass the filter, so it would
not catch a filter that admits everything. **An early-game save is required to
give it teeth.**

---

## 4. Open, ordered by consequence

1. **Survey semantics.** Blocks all deposit and planet-feature data outside own
   colonies. Settled by one paired save.
2. **Intel category thresholds from `common/`.** Currently ASSUMED from vanilla
   documentation; wrong for modded installs.
3. **Early-game save.** Without one, the fleet and system filters are asserted
   but not demonstrated. This is the cheapest high-value item — it needs no
   analysis, only a save from roughly 2250.
4. Whether `intel_level` alone gates planet detail, or survey is also required
   per-planet.
5. Whether foreign colony data is ever legitimately visible at high intel — the
   wiki says "colony info" unlocks at high and "full colony info" at full, which
   contradicts the current blanket exclusion. Needs the `common/` tables.
