"""Regression anchors for the TAG_OVERRIDES pruning pass (sweep PR F-2).

For each card touched by the cleanup, this file pins the *final*
runtime tag set — what `template.tags` should contain *after* the
auto-derivation + override pipeline runs. If the pruning is correct,
these assertions hold both before and after the change; the diff
that drops redundant override entries cannot have changed the final
tag set, because every dropped tag was already produced by the
auto-derivation.

If a future change to `OracleTextParser.classify_card_role` regresses
the auto-detection of any tag pruned here, the corresponding
assertion will fire.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Fully-redundant entries (15) — entire override set was already
# auto-derived. Each must keep its full tag set after the override
# is removed.
# ──────────────────────────────────────────────────────────────────

def test_blade_splicer_keeps_creature_etb_value_token_maker():
    tags = _tags("Blade Splicer")
    assert {"creature", "etb_value", "token_maker"} <= tags


def test_endurance_keeps_creature_etb_value_evoke_instant_speed():
    tags = _tags("Endurance")
    assert {"creature", "etb_value", "evoke", "instant_speed"} <= tags


def test_eternal_witness_keeps_creature_etb_value():
    tags = _tags("Eternal Witness")
    assert {"creature", "etb_value"} <= tags


def test_ice_fang_coatl_keeps_cantrip_creature_etb_value_instant_speed():
    tags = _tags("Ice-Fang Coatl")
    assert {"cantrip", "creature", "etb_value", "instant_speed"} <= tags


def test_omnath_keeps_creature_etb_value_threat_cantrip():
    tags = _tags("Omnath, Locus of Creation")
    assert {"creature", "etb_value", "threat", "cantrip"} <= tags


def test_snapcaster_keeps_creature_etb_value_instant_speed_early_play():
    tags = _tags("Snapcaster Mage")
    assert {"creature", "etb_value", "instant_speed", "early_play"} <= tags


def test_stoneforge_keeps_creature_etb_value_early_play_tutor():
    """Tutor came via the F-1 generic predicate; the rest from
    standard creature-ETB-value detection."""
    tags = _tags("Stoneforge Mystic")
    assert {"creature", "etb_value", "early_play", "tutor"} <= tags


def test_wall_of_omens_keeps_creature_etb_value_cantrip():
    tags = _tags("Wall of Omens")
    assert {"creature", "etb_value", "cantrip"} <= tags


def test_supreme_verdict_keeps_board_wipe_removal():
    tags = _tags("Supreme Verdict")
    assert {"board_wipe", "removal"} <= tags


def test_wrath_of_the_skies_keeps_board_wipe_removal_energy():
    tags = _tags("Wrath of the Skies")
    assert {"board_wipe", "removal", "energy"} <= tags


# ──────────────────────────────────────────────────────────────────
# Partially-redundant entries (sample) — pin the previously-dropped
# tags, confirming auto-derivation produces them.
# ──────────────────────────────────────────────────────────────────

def test_solitude_keeps_creature_etb_value_evoke_instant_speed_removal():
    """Removal is the must-keep override; the rest comes from
    creature/evoke detection."""
    tags = _tags("Solitude")
    assert {"creature", "etb_value", "evoke", "instant_speed", "removal"} <= tags


def test_galvanic_discharge_keeps_energy_instant_speed_removal():
    tags = _tags("Galvanic Discharge")
    assert {"energy", "instant_speed", "removal"} <= tags


def test_dismember_keeps_instant_speed_removal():
    tags = _tags("Dismember")
    assert {"instant_speed", "removal"} <= tags


def test_primeval_titan_keeps_creature_etb_value_threat_ramp():
    tags = _tags("Primeval Titan")
    assert {"creature", "etb_value", "threat", "ramp"} <= tags


def test_atraxa_keeps_creature_etb_value_threat_card_advantage():
    tags = _tags("Atraxa, Grand Unifier")
    assert {"creature", "etb_value", "threat", "card_advantage"} <= tags


def test_subtlety_keeps_creature_evasion_evoke_instant_speed_interaction():
    tags = _tags("Subtlety")
    assert {"creature", "evasion", "evoke", "instant_speed",
            "interaction"} <= tags
