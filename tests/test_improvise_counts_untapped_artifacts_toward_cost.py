"""Track H (2026-07-05 calibration wave, handoff from Track G's
diagnostic) — improvise/warp artifact counting is dead code.

Mechanic under test: the improvise keyword lets the caster tap
untapped artifacts to pay {1} of generic cost each; the warp
alternative cost lets an artifact-synergy spell be cast early.  Both
branches in ``engine/cast_manager.py`` tested
``'Artifact' in str(card_types)``, which NEVER matches the CardType
enum's string form (``<CardType.ARTIFACT: 'artifact'>``) — the
untapped-artifact count was always 0 and both cost paths were dead
code for every improvise/warp card in Modern.

Rules, phrased on the mechanic (no card names in test names):

1. **can_cast counts untapped artifacts toward improvise generic** —
   lands alone can't pay, lands + artifact taps can → castable.
2. **Payment taps artifacts for the shortfall** — the cast succeeds
   and taps exactly the artifacts needed beyond land capacity.
3. **effective_cmc applies the improvise discount structurally** —
   the printed keyword line is enough; no per-card cache entry
   required.
4. **Warp branch recognizes artifact presence** — the alternative
   cost path opens when the controller actually controls an artifact.

All cards are synthetic oracle text.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


_IMPROVISE_ORACLE = (
    "Improvise (Your artifacts can help cast this spell. Each "
    "artifact you tap after you're done activating mana abilities "
    "pays for {1}.)\n"
    "Whenever this creature attacks, draw a card."
)

_WARP_ORACLE = (
    "Warp {1} (You may cast this card from your hand for its warp "
    "cost. Exile this creature at the beginning of the next end "
    "step, then you may cast it from exile on a later turn.)"
)


def _template(name: str, *, generic: int, card_types,
              oracle_text: str = "", power=None, toughness=None,
              produces_mana=None) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=list(card_types),
        mana_cost=ManaCost(generic=generic),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=list(produces_mana or []),
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )


def _instance(game: GameState, tmpl: CardTemplate, controller: int,
              zone: str) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    else:
        game.players[controller].library.append(card)
    return card


def _fresh_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "SyntheticArtifacts"
    game.players[1].deck_name = "SyntheticOpponent"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    return game


def _board(game: GameState, *, lands: int, artifacts: int):
    for i in range(lands):
        _instance(game, _template(f"Synthetic Land {i}", generic=0,
                                  card_types=[CardType.LAND],
                                  produces_mana=["C"]),
                  0, "battlefield")
    for i in range(artifacts):
        _instance(game, _template(f"Synthetic Trinket {i}", generic=1,
                                  card_types=[CardType.ARTIFACT]),
                  0, "battlefield")


# G's minimal repro: 4 lands + 5 untapped artifacts, an improvise
# spell whose printed cost (8) exceeds land capacity but not
# lands + artifact taps.
_IMPROVISE_GENERIC = 8
_LANDS = 4
_ARTIFACTS = 5


def test_can_cast_counts_untapped_artifacts_toward_improvise_generic():
    game = _fresh_game()
    _board(game, lands=_LANDS, artifacts=_ARTIFACTS)
    spell = _instance(
        game, _template("Synthetic Improvise Threat",
                        generic=_IMPROVISE_GENERIC,
                        card_types=[CardType.ARTIFACT, CardType.CREATURE],
                        oracle_text=_IMPROVISE_ORACLE,
                        power=4, toughness=4),
        0, "hand")

    assert game.can_cast(0, spell), (
        "improvise must count untapped artifacts toward the generic "
        "cost: 4 lands + 5 artifact taps cover a printed 8"
    )


def test_can_cast_rejects_improvise_when_artifacts_are_tapped():
    game = _fresh_game()
    _board(game, lands=_LANDS, artifacts=_ARTIFACTS)
    for perm in game.players[0].battlefield:
        if CardType.ARTIFACT in perm.template.card_types:
            perm.tapped = True
    spell = _instance(
        game, _template("Synthetic Improvise Threat",
                        generic=_IMPROVISE_GENERIC,
                        card_types=[CardType.ARTIFACT, CardType.CREATURE],
                        oracle_text=_IMPROVISE_ORACLE,
                        power=4, toughness=4),
        0, "hand")

    assert not game.can_cast(0, spell), (
        "tapped artifacts cannot pay improvise; 4 lands alone do not "
        "cover a printed 8"
    )


def test_cast_pays_improvise_shortfall_by_tapping_artifacts():
    game = _fresh_game()
    _board(game, lands=_LANDS, artifacts=_ARTIFACTS)
    spell = _instance(
        game, _template("Synthetic Improvise Threat",
                        generic=_IMPROVISE_GENERIC,
                        card_types=[CardType.ARTIFACT, CardType.CREATURE],
                        oracle_text=_IMPROVISE_ORACLE,
                        power=4, toughness=4),
        0, "hand")

    ok = game.cast_spell(0, spell)
    assert ok, "an improvise cast payable via artifact taps must succeed"

    me = game.players[0]
    tapped_artifacts = [
        c for c in me.battlefield
        if CardType.ARTIFACT in c.template.card_types and c.tapped
    ]
    shortfall = _IMPROVISE_GENERIC - _LANDS
    assert len(tapped_artifacts) >= shortfall, (
        f"the shortfall beyond land capacity ({shortfall}) must be "
        f"paid by tapping artifacts, got {len(tapped_artifacts)}"
    )


def test_effective_cmc_applies_improvise_discount_structurally():
    """The AI-side cost model must apply the improvise discount from
    the printed keyword line alone — a card missing from the LLM tag
    cache still has the rules keyword."""
    from ai.effective_cmc import effective_cmc

    game = _fresh_game()
    _board(game, lands=_LANDS, artifacts=_ARTIFACTS)
    spell = _instance(
        game, _template("Synthetic Improvise Threat",
                        generic=_IMPROVISE_GENERIC,
                        card_types=[CardType.ARTIFACT, CardType.CREATURE],
                        oracle_text=_IMPROVISE_ORACLE,
                        power=4, toughness=4),
        0, "hand")

    cmc = effective_cmc(spell, game=game, player_idx=0)
    assert cmc == _IMPROVISE_GENERIC - _ARTIFACTS, (
        f"improvise must discount one generic per untapped artifact "
        f"({_IMPROVISE_GENERIC} - {_ARTIFACTS}), got {cmc}"
    )


def test_improvise_cannot_substitute_colorless_for_colored_pip():
    """Improvise reduces generic cost only (CR 702.125a).

    A spell with one mandatory colored pip ({U}) is uncastable when the
    controller has artifact taps to cover the generic portion but only
    colorless land sources — total_mana passes the improvise_cmc check
    (1 colorless ≥ 1) but the {U} pip cannot be satisfied.

    Root cause of classic Affinity's 23.9% regression: the improvise
    branch in can_cast returned True after a colorblind total_mana ≥
    improvise_cmc check, then tap_lands_for_mana failed silently having
    already tapped the artifacts as a side effect.
    """
    game = _fresh_game()
    # 1 colorless land + 2 untapped artifacts, but NO blue source.
    _board(game, lands=1, artifacts=2)
    # Metallic Rebuke-style: {2}{U} with Improvise.
    # Improvise reduces generic: max(1, 3-2) = 1. total_mana = 1.
    # 1 >= 1 → old code returned True (WRONG). Fix: color check fails.
    spell = _instance(
        game, _template("Synthetic Rebuke", generic=2,
                        card_types=[CardType.INSTANT],
                        oracle_text=_IMPROVISE_ORACLE,
                        # ManaCost(generic=2, blue=1) = {2}{U}
                        ),
        0, "hand")
    # Force blue pip in mana cost
    spell.template.mana_cost.blue = 1

    assert not game.can_cast(0, spell), (
        "improvise must not satisfy a mandatory blue pip with colorless "
        "mana — {2}{U} is uncastable with 2 artifacts + 1 colorless land"
    )


def test_improvise_castable_when_colored_pip_has_real_source():
    """Improvise succeeds when the mandatory colored pip has a real source.

    Same {2}{U} spell, same 2 artifacts, but the land produces U (not C).
    The colored pip is satisfied; improvise covers the generic portion.
    """
    game = _fresh_game()
    # Add a blue-producing land instead of colorless
    _instance(game, _template("Synthetic Island", generic=0,
                               card_types=[CardType.LAND],
                               produces_mana=["U"]),
              0, "battlefield")
    # Add 2 untapped artifacts
    for i in range(2):
        _instance(game, _template(f"Synthetic Mox {i}", generic=1,
                                   card_types=[CardType.ARTIFACT]),
                  0, "battlefield")
    spell = _instance(
        game, _template("Synthetic Rebuke", generic=2,
                        card_types=[CardType.INSTANT],
                        oracle_text=_IMPROVISE_ORACLE),
        0, "hand")
    spell.template.mana_cost.blue = 1

    assert game.can_cast(0, spell), (
        "improvise must allow casting {2}{U} when 1 blue land + 2 "
        "artifact taps cover the full cost"
    )


def test_warp_alternative_recognizes_artifact_presence():
    game = _fresh_game()
    _board(game, lands=2, artifacts=1)
    spell = _instance(
        game, _template("Synthetic Warp Threat", generic=6,
                        card_types=[CardType.CREATURE],
                        oracle_text=_WARP_ORACLE,
                        power=3, toughness=3),
        0, "hand")

    assert game.can_cast(0, spell), (
        "the warp alternative-cost branch must recognize a real "
        "artifact on the battlefield (2 lands cannot hardcast a 6)"
    )
