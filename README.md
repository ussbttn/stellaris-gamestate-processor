# Stellaris Gamestate Processor

Watches Stellaris autosaves, applies fog-of-war filtering, and writes files a
SillyTavern extension injects into an LLM's context. The point is an in-world
advisor that gives real strategic advice **without knowing anything the player
doesn't know**.

No LLM runs here. This is a file converter. All AI happens in SillyTavern.

---

## Status

| Piece | State |
|---|---|
| Save parsing (`.sav` and raw gamestate) | **works** — 1s on a 48MB save |
| Intel extraction (per-system + per-empire) | **works, verified** |
| Fleet visibility filter | **works, verified**, untested on early saves |
| Redaction / projection layer | **works** for what it covers |
| `briefing.txt` generation | **works** — narrow, ~500 tokens |
| Watcher loop | **works** |
| `state.json` full FoW-filtered output | **not built** |
| Tool query interface | **not built** |
| Survey gate | **blocked** — semantics unresolved |
| `common/` vocabulary + localisation | **not built** |
| `.exe` + GUI launcher | **not built** |

Verified against Pegasus v4.4.6 at 2396, 2397, 2398.

## Read first

**`docs/REDACTION_CONTRACT.md`** is the specification. It defines, per field,
what must be true for the player to know it, and marks each gate VERIFIED,
ASSUMED, or UNRESOLVED. Anything UNRESOLVED is not extracted.

Its governing rule: **errors here are asymmetric.** Omitting data the player
should have makes the advisor worse and you will notice. Including data they
shouldn't have makes it cheat and *you will not notice*. So the default is
omission.

**`docs/STATE_MAP.md`** catalogues all 96 top-level gamestate structures against
save paths, token costs, and gates.

## Layout

```
src/
  extract_intel.py     intel vectors; targeted, not a general parser
  intel_projection.py  IntelLevel, VISIBILITY, Redactor — the gate
  game_state.py        economy/military/contacts + briefing renderer
  watcher.py           watch saves -> write briefing.txt + audit.json
tools/
  schema_diff.py       schema dump / cardinality search / save diff
```

## Run

```bash
# one pass, inspect the output
python src/watcher.py --saves "Example Save Files" --out ./out --once
cat out/briefing.txt

# continuous
python src/watcher.py \
  --saves "~/Documents/Paradox Interactive/Stellaris/save games" \
  --out   "~/SillyTavern/data/default-user/extensions/stellaris-advisor"
```

Two files are written, and the split is a safety property, not tidiness:

- **`briefing.txt`** — goes into the model's context.
- **`audit.json`** — read only by the extension, never injected. Holds names of
  empires the player has *not* met, so a reply mentioning one can be flagged.
  This is precisely the data being kept from the model, so it must never share a
  file with the briefing.

## Reverse-engineering workflow

Field names lie. Cardinality and change-over-time do not.

```bash
# every path sized ~604 -> candidates for "indexed by system"
python tools/schema_diff.py find <save> 604

# what changed between two saves
python tools/schema_diff.py diff <save_a> <save_b>
```

To settle what a field means: save, perform **one** known action, save again,
diff. The path that gained exactly one entry is the field. This is how the
survey gate gets resolved, and it is the only method that works — `common/`
does not define runtime state.

Caveat: numeric keys collapse to `[]`, so per-country arrays aggregate across
all empires. Good for detecting change, misleading for cardinality matching.

## Known-wrong things, kept visible

- **Survey semantics unresolved.** `surveyed_deposit_holders` was read as
  "bodies surveyed by the player (88%)". Not defensible: the top twelve empires
  cluster in 5,027–5,531 despite known-system counts of 428–601, and the count
  exceeds the 2,809 bodies that have deposits at all. Deposits outside own
  colonies are therefore not extracted.
- **Intel category thresholds are ASSUMED** from vanilla docs. The five
  categories are derived from one 0–100 score, not stored, and mods rewrite the
  thresholds.
- **Empire names are approximate.** Recursive localisation templates; proper
  rendering needs `localisation/*.yml`. "Blessed Azanti Imperium" for
  "Blessed Azantian Imperium".
- **`military_power` unvalidated** against the in-game fleet total.
- **Fleet filter effectively untested.** At 2396 the empire sees 93% of fleets,
  so a filter admitting everything would pass. Needs an early save.
