"""Non-land permanents with a tap-for-mana ability must produce mana.

The engine populated `produces_mana` only for lands, and the mana-payment
path tapped only lands (plus a Mox-metalcraft special-case and artifact
improvise-for-generic). Every ordinary mana rock (Talisman, Signet, Mind
Stone) and every mana creature (Birds of Paradise, Llanowar Elves, Devoted
Druid) therefore produced ZERO usable mana — they could not help pay for a
spell. This crippled every ramp/dork deck at the most basic level and made
mana-creature combos (Devoted Druid + Vizier) impossible to even begin.

Rule under test (CR 605 mana abilities): a permanent with a plain
"{T}: Add {mana}" ability — no additional cost, no restriction — is a mana
source. Its controller may tap it (if untapped and not summoning-sick for a
creature) to pay a spell's cost, exactly like a land.

Class size: every mana rock and mana dork in the format. Card names here are
fixture carriers only; the engine change is oracle-driven, no name gates.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _game_with(sources, spell_name, db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    p = game.players[0]

    def add(name, zone):
        t = db.get_card(name)
        assert t is not None, f"missing card: {name}"
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone=zone)
        c._game_state = game
        if zone == "battlefield":
            c.enter_battlefield()
            c.summoning_sick = False  # not sick — can tap for mana
        getattr(p, "battlefield" if zone == "battlefield" else zone).append(c)
        return c

    for s in sources:
        add(s, "battlefield")
    spell = add(spell_name, "hand")
    return game, spell


# ── Template population ──────────────────────────────────────────────────────

def test_mana_rock_template_produces_mana(card_db):
    """A mana rock's template must expose the colors it taps for."""
    tal = card_db.get_card("Talisman of Impulse")
    assert tal is not None
    # Talisman of Impulse: {T}: Add {C}. / {T}: Add {R} or {G}...
    produced = set(tal.produces_mana)
    assert produced, "Talisman of Impulse must produce mana (had empty produces_mana)"
    assert {"R", "G"} & produced, f"expected R/G production, got {produced}"


def test_mana_dork_template_produces_mana(card_db):
    """A mana creature's template must expose the colors it taps for."""
    birds = card_db.get_card("Birds of Paradise")
    llan = card_db.get_card("Llanowar Elves")
    assert set(birds.produces_mana) == {"W", "U", "B", "R", "G"}, (
        f"Birds taps for any color; got {birds.produces_mana}")
    assert "G" in llan.produces_mana, (
        f"Llanowar Elves taps for G; got {llan.produces_mana}")


def test_non_mana_creature_has_no_mana(card_db):
    """Negative pin: a creature with no tap-for-mana ability stays empty."""
    rag = card_db.get_card("Ragavan, Nimble Pilferer")
    assert rag.produces_mana == [], (
        f"Ragavan has no plain mana ability; got {rag.produces_mana}")


# ── Payment: the source can actually pay ─────────────────────────────────────

def test_mana_rock_can_pay_for_spell(card_db):
    """A single Talisman must let its controller cast a 1-mana R spell."""
    game, bolt = _game_with(["Talisman of Impulse"], "Lightning Bolt", card_db)
    assert game.can_cast(0, bolt) is True, (
        "Talisman of Impulse (taps for R) must pay for Lightning Bolt {R}")


def test_mana_dork_can_pay_for_spell(card_db):
    """A single Llanowar Elves must let its controller cast a 1-mana G spell."""
    game, elf = _game_with(["Llanowar Elves"], "Llanowar Elves", card_db)
    assert game.can_cast(0, elf) is True, (
        "Llanowar Elves (taps for G) must pay for a {G} one-drop")


def test_birds_pays_any_color(card_db):
    """Birds of Paradise taps for any color — pays a {R} spell alone."""
    game, bolt = _game_with(["Birds of Paradise"], "Lightning Bolt", card_db)
    assert game.can_cast(0, bolt) is True, (
        "Birds of Paradise (any color) must pay for Lightning Bolt {R}")


def test_summoning_sick_dork_cannot_pay(card_db):
    """A mana creature that is summoning-sick cannot tap for mana (CR 302.6)."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    p = game.players[0]
    t = card_db.get_card("Birds of Paradise")
    birds = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
    birds._game_state = game
    birds.enter_battlefield()
    birds.summoning_sick = True  # just entered — cannot tap
    p.battlefield.append(birds)
    bolt_t = card_db.get_card("Lightning Bolt")
    bolt = CardInstance(template=bolt_t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="hand")
    bolt._game_state = game
    p.hand.append(bolt)
    assert game.can_cast(0, bolt) is False, (
        "A summoning-sick mana creature must not be usable for mana")
