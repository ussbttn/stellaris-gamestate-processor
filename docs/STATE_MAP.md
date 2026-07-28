# Player-facing state → save file map

Derived from **Pegasus v4.4.6**, 604-system galaxy, year 2396, 48MB gamestate.

## Method

Worked save → wiki, not wiki → save. The gamestate has **96 unique top-level
keys** — a complete, finite enumeration of everything the game persists. The
wiki was then used to classify each as player-facing or engine-internal, and to
catch UI surfaces I might otherwise have missed. Going wiki-first would have
risked omitting anything nobody documented.

Wiki cross-check used: Main interface (top bar, outliner's four tabs, F1–F10
menus), Empire interface (ruler, ethics, civics, authority, origin, diplomatic
weight, modifiers, demographics, expansion planner), Situations.

## Legend

**Gate** — what must be true for the player to know this.
`self` = own empire, always visible · `intel≥N` = system intel level ·
`contact` = empire must be met · `surveyed` = system must be surveyed ·
`none` = public knowledge (galactic community, market)

---

## A. Own empire — always visible, no gating

| What | Save location | Count here | ~tokens |
|---|---|---|---|
| Resource stockpiles | `country[P].modules.standard_economy_module.resources` | 20 | 120 |
| Income/expense/balance by source | `country[P].budget.{current,last}_month` | 35 sources | 400 |
| **Timeline (empire history)** | `country[P].timeline_events` | **248 (51 real)** | **750** |
| Active event chains / POIs | `country[P].events.poi` | 12.5KB | 300 |
| Scripted flags | `country[P].flags` | 15KB | *internal* |
| Technologies known | `country[P].tech_status` | 540 | 2,200 |
| Traditions / ascension perks | `country[P].traditions`, `.ascension_perks` | 50 / 9 | 250 |
| Relics | `country[P].relics` | 5 | 40 |
| Edicts active | `country[P].edicts` | 201 | 400 |
| Ethics / civics / authority / origin | `country[P].{ethos,government}` | — | 120 |
| Ruler + council | `country[P].ruler`, `council_positions` | — | 150 |
| Leaders owned | `country[P].owned_leaders` → `leaders[]` | 21 | 500 |
| Sectors | `country[P].sectors` → `sectors[]` | 5 | 80 |
| Factions (internal politics) | `pop_factions[]` filtered to player | — | 200 |
| Espionage networks run | `spy_networks[]` where `owner=P` | — | 150 |
| Fleet templates / ship designs | `country[P].ship_design_collection` | — | 300 |
| Naval capacity, empire size, sprawl | `country[P].{used_naval_capacity,empire_size}` | — | 40 |

**Subtotal ≈ 6,000 tokens.**

## B. Own colonies — always visible, and cheap here

You have **6 colonies**. Galaxy-wide there are 129, so filtering to the player
matters enormously.

| What | Save location | Note |
|---|---|---|
| Colony record | `colony[]` where owner = player | 129 total, 6 yours |
| Planet detail | `planets.planet[]` | 4.3MB galaxy-wide |
| Districts | `districts[]` | 370 total |
| **Zones** | `zones[]` | 539 — the 4.x zone system |
| Buildings | `buildings[]` | 1,290 total |
| Pop groups | `pop_groups[]` | 2,505 — the 4.0 pop rework |
| Jobs | `pop_jobs[]`, `colony[].pop_jobs` | |
| Governor | `colony[].governor` | |

**≈ 1,800 tokens for six colonies in full detail.** Cheap enough to always
include. Would be prohibitive for a wide empire — needs to scale by colony
count, not be assumed.

## C. Diplomacy — gated on `contact`

| What | Save location | Gate |
|---|---|---|
| Known empires | `country[P].intel_manager.intel` | contact |
| Believed relative power | `.intel_manager.intel[].stale_intel` | contact |
| Relations / opinion / trust | `country[P].relations_manager` | contact |
| First contact in progress | `first_contacts.contacts[]`, `country[P].first_contact` | — |
| Federation | `federation[]` | member or known |
| Agreements (subject/overlord) | `agreements.agreements[]` | party or known |
| Truces / trade deals | `truce[]`, `trade_deal[]` | party |
| Wars | `war[]` — goals, exhaustion, participants | party or known |
| Galactic Community | `galactic_community` — members, council, proposed, passed | none |
| Resolutions | `resolution[]` | none |
| Market prices | `market.fluctuations`, `.galactic_market_*` | none |

58 contacts here. **≈ 4,400 tokens** at full detail; ~600 as one-liners.

## D. Known space — gated on `intel_level` / `surveyed`

**This is the whole cost problem.** 583 known systems of 604.

| What | Save location | Gate |
|---|---|---|
| System name / position / hyperlanes | `galactic_object[id]` | intel≥1 |
| Owner | `galactic_object[id].owner` | intel≥1 |
| Planets in system | `galactic_object[id].planet[]` | intel≥2 |
| **Deposits** | `deposit[]` (4,574 galaxy-wide) | **surveyed** |
| Starbase | `starbase_mgr.starbases[]` (538; keyed by starbase id) | intel≥2 |
| Megastructures | `megastructures[]` (356) | intel≥2 |
| Fleets present | `fleet[]` (2,785), `ships[]` (5,725) | **`sensor_range_fleets` — solved** |
| Armies | `army[]` | intel≥4 |
| Archaeological sites | `archaeological_sites` | surveyed |
| Astral rifts | `astral_rifts` | discovered |
| Bypasses / wormholes / clusters | `bypasses`, `natural_wormholes`, `clusters` | intel≥1 |
| Storms | `storms`, `storm_influence_fields` | intel≥1 |
| Nebulae | `nebula` (7 blocks) | intel≥1 |

**≈ 15,000 tokens minimal, 47,000 rich.** Must be tool-queried, not injected.
Your 104 *controlled* systems are ~8,000 of that and are the promotion
candidate if the model reaches for them constantly.

## E. Events and notifications — what you flagged

The save does **not** hold a full event history. It holds four distinct things,
and conflating them would have produced a badly wrong briefing:

| Structure | What it actually is | Count |
|---|---|---|
| `country[P].timeline_events` | **Curated history.** Dated, definition-keyed, 2200→2396. The in-game Timeline. Player-facing by construction. | 248 (197 are year markers) |
| `player_event` | **Pending/scheduled** events, dates in the *future* (2396–2399) | 7 |
| `message` | **Live notification queue.** Carries `game_text_variables` with *pre-resolved* display strings | 4 |
| `fired_event_ids` | Sparse dedup list, not a log | 19 |
| `situations` | Ongoing situations with `type` + `progress` (0–100) + approach | keyed by country |

Two consequences:

**`timeline_events` is the answer to "what has happened."** 51 meaningful entries
across 196 years, ~750 tokens. Belongs in the always-context tier.

**`message` solves part of the name problem.** It stores resolved strings
(`"Martial Order of the Thorn"`, `"Imperialist Faction"`) rather than raw
localisation keys. Anywhere a notification is being rendered, no localisation
files are needed.

## F. Omniscient — must never be extracted

| Structure | Why |
|---|---|
| `country[]` for unmet empires | existence itself is privileged |
| `dead_country`, `dead_war`, `dead_leader`, `dead_fleet`, … (12 blocks) | historical omniscience |
| `fleet[]` / `ships[]` outside sensor range | position and composition |
| `deposit[]` in unsurveyed systems | survey is the gate |
| `spy_networks[]` where `owner != P` | others' espionage against you is partly hidden |
| `pop_groups[]` on foreign worlds | |
| `saved_event_target` (≈60 blocks), `flags` | engine internals |

---

## Revised tiering

| Tier | Contents | ~tokens |
|---|---|---|
| **Always in context** | A + B + timeline + situations + wars + contact one-liners | **~3,000** |
| **`state.json` on disk** | everything above except F | ~30,000 |
| **Tool-queried** | section D, per-colony deep detail, per-empire detail | on demand |

That is roughly 6× my current briefing, and still a fraction of a full dump.

## Fog of war — resolved

The sensor question that has been the standing risk on this project is settled.
`country[P]` carries explicit visibility lists; nothing needs recomputing from
sensor ranges.

| Field | Size here | What it is |
|---|---|---|
| **`sensor_range_fleets`** | 2,597 ids | **Exactly the fleets the player can see.** All 2,597 resolve to live fleet ids (2597/2597), and the max matches the fleet id space. Filter `fleet[]` through this and fleet visibility is correct by construction. |
| **`surveyed_deposit_holders`** | ~21,800 tokens | Explicit list of surveyed deposit holders — the survey gate for `deposit[]`, stated rather than inferred. Large, so index it; never inject it. |
| `terra_incognita` | `size=256` + RLE data | The rendered fog bitmap. Redundant given `intel_level`; ignore. |
| `systems_with_forced_visible_fleets` | 3 systems | Override for scripted reveals. Union it with the above. |
| `fog_machine` | empty | Nothing to extract. |

2,597 of 2,779 fleets are visible in this save — 93%, which is expected for a
dominant late-game empire with sensors everywhere. On an early-game save this
list would be short, and that is precisely the case worth testing against.

## Remaining open questions

1. Whether `intel_level` alone gates planet detail, or `surveyed` is also
   required per-planet. `surveyed_deposit_holders` probably answers this too.
2. Whether `starbase_mgr`, which is keyed by country, needs the same
   generation-handle care as `intel_manager` did.
3. Whether `pop_groups` on foreign colonies is ever legitimately visible at
   high intel, or never.


---

# Addendum: three structures resolved

## Survey and intel are orthogonal — confirmed

They are separate systems answering separate questions, and neither substitutes
for the other.

| System | Scope | Gates |
|---|---|---|
| `country[P].surveyed_deposit_holders` | per **celestial body** | `planet[].deposits`, planet features |
| `intel_level[sysid]` (0–4) | per **system** | system contents |
| `intel_manager.intel[cid]` (0–100) | per **empire** | economy, tech, diplomacy |

**5,410 entries here — the player has surveyed 5,363 of 6,085 bodies (88%).**

Careful: `planet[].surveyed_by` is a **scalar, not a list** — it records the
*first* empire to survey the body, not everyone who has. Using it as a
visibility check would be wrong in both directions. The player's own survey
record is `surveyed_deposit_holders`.

## `starbase_mgr` — 538 starbases, no owner field

`starbase_mgr.starbases[]`, keyed by starbase id (an earlier note in this
document said "keyed by country" — that was wrong).

Fields: `level` (outpost→citadel), `type`, `modules`, `buildings`, `orbitals`,
`build_queue`, `shipyard_build_queue`, `construction_type`.

There is **no owner and no system field.** Resolution requires two hops:

- system → starbase: `galactic_object[sysid].starbases`
- starbase → owner: `.station` → `ships[]` → `.fleet` → owner

and ownership itself is an **inverted index**: `country[cid].fleets_manager.owned_fleets`
lists fleets per country, so a fleet→owner map must be built once per parse.
This is the same shape as system ownership, which also has no direct field.

Player here: `starbase_capacity` 16, `starbase_capacity_used` 8 — against 104
controlled systems, since outposts do not consume capacity.

## `pop_groups` — the 4.0 population accounting unit

Not species, not templates, not factions. A pop_group is **all pops on one
planet sharing (species, stratum, ethic, faction)**. 2,460 galaxy-wide.

```
key = { species=170  category="worker"
        ethos={ ethic="ethic_authoritarian" }  pop_faction=16777230 }
planet=36  size=150  happiness=0.275  power=3.9  crime=2.17
habitability=0.55  amenities_usage=217.5  housing_usage=150
```

So a single record carries species, stratum, **ethic, faction, and happiness**
together — precisely the data that should not be readable inside foreign
borders. `pop_groups` is a flat global list with no per-observer view, so
**foreign pop_groups are privileged in full** and must be filtered by planet
ownership before extraction. Only groups on the player's 6 colonies qualify.

## Remembered hostile forces

`country[P].intel` (119KB, 41 systems) is separate from everything above: it
stores **last-known hostile forces** per system — name, coordinate,
`military_power`, owner, and ship-class composition.

This is the game's own "as of your last scouting" record, and it pairs with
`sensor_range_fleets` to cover both halves of fleet visibility:

- currently visible → `sensor_range_fleets`
- remembered but not visible → `country[P].intel[].hostile`

Both are explicit. Neither needs inferring.
