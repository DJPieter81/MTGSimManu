"""The "enters or attacks" oracle template carries a real attack trigger.

Root cause (Amulet Titan mechanic audit, 2026-08-25): `parse_has_attack_trigger`
matched only "Whenever this creature attacks" and the self-named "Whenever
<Name> attacks". Modern's combined template — "Whenever this creature **enters
or attacks**, …" — was not recognised, so 88 Modern creatures carry
`has_attack_trigger == False` despite having an attack trigger.

Two consequences, both verified before this fix:
  * Engine: the attack-time land-search dispatch is gated on the flag, so a
    6-mana ramp finisher's attack trigger silently did nothing.
  * AI: `creature_threat_value` credits `has_attack_trigger` as virtual power,
    and four `ai/ev_player.py` call sites consult it when deciding whether an
    attack is worthwhile — so every card on this template was undervalued.

Note the flag is NOT what dispatches generic oracle attack triggers — those go
through an unconditional `resolve_attack_trigger` call and already worked. This
fixes the flag-gated paths and the valuation, not the generic dispatch.

Rule under test: a creature whose oracle uses the combined "enters or attacks"
template parses as having an attack trigger, in both the self-referential and
self-named forms. Mechanic-driven (oracle template), no card names asserted.
"""
from __future__ import annotations

from engine.oracle_parser import parse_has_attack_trigger


def test_combined_enters_or_attacks_template_is_an_attack_trigger():
    assert parse_has_attack_trigger(
        "Whenever this creature enters or attacks, create two 2/2 black "
        "Zombie creature tokens.")


def test_combined_template_self_named_form():
    assert parse_has_attack_trigger(
        "Whenever Grave Titan enters or attacks, create two 2/2 black Zombie "
        "creature tokens.", "Grave Titan")


def test_combined_template_short_legendary_name_form():
    # Legendaries with a title refer to themselves by the text BEFORE the
    # comma ("Agrus Kos, Spirit of Justice" -> "Whenever Agrus Kos ...").
    assert parse_has_attack_trigger(
        "Whenever Agrus Kos enters or attacks, tap target creature.",
        "Agrus Kos, Spirit of Justice")


def test_every_real_enters_or_attacks_card_parses_as_an_attack_trigger(card_db):
    """Whole-class check against the real card DB, not invented strings.

    The bug was a template the parser had never seen, so the regression guard
    that matters is "no card carrying this template is left unflagged".
    """
    import re

    # Use the session-cached DB fixture rather than a fresh CardDatabase()
    # load: the suite already holds one, so a second cold load here just
    # doubled load time and risked the 120s per-test cap on slow runners.
    db = card_db
    templates = list(db.cards.values() if hasattr(db, "cards") else [])
    # SELF-referential subject only ("this <noun>" or the card's own name).
    # "Whenever your commander enters or attacks" is a WATCHER on a different
    # permanent and must stay unflagged — that exclusion is the discriminator,
    # not an oversight.
    self_ref = re.compile(r"whenever this \w+ enters or attacks")

    missed, wrongly_claimed = [], []
    for t in templates:
        oracle = (t.oracle_text or "").lower()
        if "enters or attacks" not in oracle:
            continue
        flagged = bool(getattr(t, "has_attack_trigger", False))
        if self_ref.search(oracle):
            if not flagged:
                missed.append(t.name)
        elif "whenever your commander enters or attacks" in oracle and flagged:
            wrongly_claimed.append(t.name)

    assert not missed, (
        f"{len(missed)} card(s) use a SELF-referential 'enters or attacks' "
        f"template but are not flagged: {missed[:10]}")
    assert not wrongly_claimed, (
        f"watcher-form triggers must not be claimed as this card's own attack "
        f"trigger: {wrongly_claimed[:10]}")


def test_plain_attack_template_still_parses():
    # Regression: the original two forms must keep working.
    assert parse_has_attack_trigger(
        "Whenever this creature attacks, create a 1/1 Goblin token.")
    assert parse_has_attack_trigger(
        "Whenever Ragavan attacks, create a Treasure token.",
        "Ragavan, Nimble Pilferer")


def test_enters_only_template_is_not_an_attack_trigger():
    # "enters" alone is an ETB, not an attack trigger — must not be claimed.
    assert not parse_has_attack_trigger(
        "Whenever this creature enters, draw a card.")


def test_other_creatures_attack_trigger_is_not_claimed():
    # Triggers watching OTHER creatures attacking belong to a different class;
    # the self-anchor is the discriminator.
    assert not parse_has_attack_trigger(
        "Whenever a creature you control enters or attacks, you gain 1 life.")


def test_ramp_finisher_attack_land_search_fires():
    """End-to-end: the flag-gated attack land-search actually executes."""
    import random
    from engine.cards import CardInstance
    from engine.game_state import GameState, Phase
    from engine.card_database import CardDatabase
    from engine.triggers import TriggerManager

    db = CardDatabase()

    def add(game, name, ctrl, zone):
        t = db.get_card(name)
        assert t is not None, f"missing {name}"
        c = CardInstance(template=t, owner=ctrl, controller=ctrl,
                         instance_id=game.next_instance_id(), zone=zone)
        c._game_state = game
        if zone == "battlefield":
            c.enter_battlefield()
            c.summoning_sick = False
        getattr(game.players[ctrl],
                "battlefield" if zone == "battlefield" else zone).append(c)
        return c

    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 8
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Boros Energy"
    for _ in range(6):
        add(game, "Forest", 0, "library")
    attacker = add(game, "Primeval Titan", 0, "battlefield")
    assert attacker.template.has_attack_trigger, (
        "the combined template must set the flag the dispatch gates on")

    before = len([c for c in game.players[0].battlefield
                  if c.template.is_land])
    TriggerManager.trigger_attack(game, attacker, 0)
    after = len([c for c in game.players[0].battlefield
                 if c.template.is_land])
    assert after > before, (
        f"a ramp finisher's attack trigger must put lands onto the "
        f"battlefield ({before} -> {after})")
