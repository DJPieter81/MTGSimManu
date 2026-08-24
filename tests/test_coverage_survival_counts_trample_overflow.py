"""Coverage-pass survival math must count trample overflow through a blocker.

Trample (CR 702.19): a blocked attacker with trample assigns lethal damage to
its blockers and the rest to the player. The joint `coverage_pass` decided
whether the defender was already safe by summing the power of only the
attackers NOT in the block map — a *blocked* trampler was removed from that sum
entirely, so its through-damage (power minus its blockers' combined toughness)
counted as zero. The pass then stopped forcing coverage one attacker too early,
leaving a second attacker unblocked, and the defender died to overflow it never
accounted for.

Rule under test: given an `overflow_fn`, `coverage_pass` adds each blocked
trampler's through-damage back into the survival total, so it keeps assigning
blockers until the *actual* incoming damage (unblocked power + trample
overflow) is survivable. Mechanic-driven (trample overflow), no card names.
"""
from __future__ import annotations

from ai.block_assignment import coverage_pass


class _Fake:
    def __init__(self, iid, power, toughness, trample=False):
        self.instance_id = iid
        self.power = power
        self.toughness = toughness
        self.trample = trample


def test_blocked_trampler_overflow_still_forces_second_coverage():
    # Defender at 5 life. Attackers: a trample 6/6 and a vanilla 4/4.
    # Two 0/3 walls available. Blocking only the trampler lets 6-3=3 punch
    # through; plus the unblocked 4/4 = 7 total >= 5 → lethal. Coverage must
    # field BOTH walls (trampler overflow 3 + vanilla blocked 0 = 3 < 5).
    trampler = _Fake("A", 6, 6, trample=True)
    vanilla = _Fake("B", 4, 4)
    walls = [_Fake("w1", 0, 3), _Fake("w2", 0, 3)]
    sorted_attackers = [trampler, vanilla]  # biggest power first

    id_to_blocker = {b.instance_id: b for b in walls}

    def overflow_fn(attacker, chosen_blockers):
        if not attacker.trample:
            return 0
        return max(0, (attacker.power or 0)
                   - sum((b.toughness or 0) for b in chosen_blockers))

    blocks, used = coverage_pass(
        sorted_attackers, walls,
        my_life=5,
        can_block_fn=lambda a, b: True,
        cost_fn=lambda b: 0.0,
        overflow_fn=overflow_fn,
    )

    # Both attackers must be covered — leaving the vanilla open is lethal once
    # the trampler's 3 overflow is counted.
    assert vanilla.instance_id in blocks, (
        "coverage left the vanilla attacker unblocked because it under-counted "
        "the blocked trampler's overflow — that is the fatal miscount")
    assert trampler.instance_id in blocks
    # Resulting incoming = trample overflow 3 + 0 = 3 < 5 → defender survives.


def test_overflow_fn_absent_preserves_legacy_sum():
    # Without an overflow_fn the survival math is the plain unblocked-power sum
    # (unchanged behaviour for non-trample boards).
    a = _Fake("A", 6, 6)
    b = _Fake("B", 4, 4)
    walls = [_Fake("w1", 0, 8)]  # one wall, blocks the 6/6

    blocks, used = coverage_pass(
        [a, b], walls,
        my_life=5,
        can_block_fn=lambda x, y: True,
        cost_fn=lambda x: 0.0,
    )
    # 6/6 blocked (no trample → 0 through); 4/4 unblocked = 4 < 5 → safe, stop.
    assert a.instance_id in blocks
    assert b.instance_id not in blocks
