"""Intel score -> per-category level, read from common/intel_levels."""
BANDS = [
    # (lo, hi, government, diplomacy, economy, technology, military)
    (0, 9, 0,0,0,0,0), (10,19, 1,0,0,0,0), (20,29, 1,1,0,0,0),
    (30,39, 1,1,1,1,1), (40,49, 2,1,1,1,1), (50,59, 2,2,1,1,1),
    (60,69, 2,2,2,2,2), (70,79, 3,2,2,2,2), (80,89, 3,3,2,2,2),
    (90,98, 3,3,3,3,3), (99,100, 4,4,4,4,4),
]
CATS = ("government","diplomacy","economy","technology","military")
def levels(score: float) -> dict:
    # Bands are integer ranges ({70 79}); a raw score of 79.93 sits between
    # bands unless floored. Flooring matches the displayed integer intel.
    score = int(score)
    for lo,hi,*v in BANDS:
        if lo <= score <= hi:
            return dict(zip(CATS, v))
    return dict(zip(CATS, (4,4,4,4,4)))
COLONY = {0:None, 1:"colonies_low", 2:"colonies_med", 3:"colonies_high + resource_production", 4:"colonies_full"}
