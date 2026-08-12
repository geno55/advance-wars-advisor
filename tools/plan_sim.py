"""Simulate recording strategies now that the luck roll is known to VARY.

The player confirmed that replaying from a save state yields different defender
health, so the RNG is not restored -- each battle draws a fresh roll. That makes
repeats informative again: repeating one attack samples the 0..9 range and
reveals the min/max damage for that configuration, which is a tight constraint.

Question this answers: for a fixed budget of battles, is it better to run many
distinct matchups once, or fewer matchups several times each?

Success is prediction-equivalence, not a single surviving hypothesis -- see
calibrate.agreement().
"""
import itertools
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tests"))

from calibrate import Obs, agreement, outcome, predict  # noqa: E402
from engine.damage import VARIANTS  # noqa: E402

TERRAINS = ["road", "plains", "woods", "mountain"]
PLAN = [
    ("MdTank", "Infantry", "road"), ("MdTank", "Infantry", "plains"),
    ("AntiAir", "Infantry", "road"), ("AntiAir", "Infantry", "plains"),
    ("Tank", "Infantry", "road"), ("Tank", "Infantry", "plains"),
    ("Tank", "Recon", "woods"), ("Tank", "Recon", "mountain"),
    ("MdTank", "Tank", "woods"), ("MdTank", "Tank", "mountain"),
    ("Tank", "Mech", "woods"), ("Tank", "Mech", "mountain"),
]
TRUTH_STARS = {"road": 0, "plains": 1, "woods": 2, "mountain": 4}


def probe(a, d, t, ahp=100):
    return Obs({"attacker": a, "defender": d, "att_hp": str(ahp),
                "def_hp": "100", "terrain": t, "mode": "exact",
                "observed": "0"}, 0)


def done(alive):
    """Practical success: terrain stars pinned, and no surviving hypothesis
    disagrees about kill/no-kill.

    Requiring identical exact damage is the wrong bar -- it demands the variants
    be the same function, which only floor_end and floor_attack_then_end are.
    What an advisor asserts is 'this kills', so that is what must be unambiguous;
    a residual 1-2 HP spread on non-lethal predictions is tolerable.
    """
    if not alive:
        return False
    if len({tuple(sorted(h[1].items())) for h in alive}) != 1:
        return False
    _, _, kill, _ = agreement(alive)
    return kill == 0


def run(order, truth, rng, budget):
    hyps = [(v, dict(zip(TERRAINS, c)))
            for v in VARIANTS
            for c in itertools.product(range(5), repeat=len(TERRAINS))]
    for n, battle in enumerate(order[:budget], 1):
        a, d, t = battle[0], battle[1], battle[2]
        ahp = battle[3] if len(battle) > 3 else 100
        p = probe(a, d, t, ahp)
        obs_val = outcome(p, truth[0], truth[1][t], rng.randrange(10))
        hyps = [h for h in hyps if obs_val in predict(p, h[0], h[1][t])]
        if done(hyps):
            return n, len(hyps)
    return None, len(hyps)


def residual(order, truth, rng):
    """Run the whole plan, then report what ambiguity is LEFT.

    Pass/fail on 'zero disagreement' turned out to be the wrong lens: a fixed
    plan rarely reaches it, but the leftover is often tiny. What matters is how
    often the survivors would actually give conflicting advice.
    """
    hyps = [(v, dict(zip(TERRAINS, c)))
            for v in VARIANTS
            for c in itertools.product(range(5), repeat=len(TERRAINS))]
    for battle in order:
        a, d, t = battle[0], battle[1], battle[2]
        ahp = battle[3] if len(battle) > 3 else 100
        p = probe(a, d, t, ahp)
        val = outcome(p, truth[0], truth[1][t], rng.randrange(10))
        hyps = [h for h in hyps if val in predict(p, h[0], h[1][t])]
    stars_ok = len({tuple(sorted(h[1].items())) for h in hyps}) == 1
    scen, _, kill, spread = agreement(hyps)
    return len(hyps), stars_ok, 100.0 * kill / scen, spread


def strategy(name, order, trials=8):
    kills, spreads, nstars, sizes = [], [], 0, []
    for seed in range(trials):
        rng = random.Random(seed)
        truth = (list(VARIANTS)[seed % len(VARIANTS)], TRUTH_STARS)
        n, stars_ok, killpct, spread = residual(order, truth, rng)
        kills.append(killpct)
        spreads.append(spread)
        sizes.append(n)
        nstars += 1 if stars_ok else 0
    kills.sort()
    print(f"  {name:26s} {len(order):3d} battles -> "
          f"{min(sizes)}-{max(sizes)} left, stars pinned {nstars}/{trials}, "
          f"kill-disagreement {kills[0]:.1f}-{kills[-1]:.1f}% "
          f"(median {kills[len(kills)//2]:.1f}%), max spread {max(spreads)} HP")


# Damaged attackers are usable now that exact HP is readable from RAM. The
# variants differ mainly in HOW damage scales with attacker HP, so full-health
# battles alone can never separate them.
HURT = [(a, d, t, hp)
        for (a, d, t) in PLAN[:6]
        for hp in (73, 46, 28)]

print("luck VARIES per battle (confirmed empirically). exact-HP observations.\n")
print("full-health attackers only:")
strategy("  12 distinct, 1 each", list(PLAN))
strategy("  12 distinct x 2 repeats", [b for b in PLAN for _ in range(2)])
print("\nwith damaged attackers mixed in:")
strategy("  12 full + 18 damaged", list(PLAN) + HURT)
strategy("  6 full + 18 damaged", list(PLAN[:6]) + HURT)
strategy("  6 full + 9 damaged", list(PLAN[:6]) + HURT[:9])
strategy("  4 full + 6 damaged", list(PLAN[:4]) + HURT[:6])
