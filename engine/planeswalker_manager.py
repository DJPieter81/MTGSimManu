"""
Planeswalker manager — loyalty-ability activation (CR 606).

The rule this module owns:

    A loyalty ability's effect resolves from its PRINTED oracle text
    through the same oracle-driven machinery every other effect surface
    uses (`engine.target_solver` for targeting, the `zone_mgr` funnel for
    zone changes); an effect the resolver cannot execute is REFUSED
    BEFORE the loyalty is paid.

That second half mirrors `ActivationManager.can_activate` rule 9b — "an
effect kind the resolver cannot execute must be refused before any cost
is charged" — an invariant this path used to violate.  It deducted
loyalty FIRST and then dispatched through a hand-written chain of
substring tests against the ability description, five of whose branches
were keyed on vocabulary no Magic card prints ("bounce", "brainstorm",
"cast sorceries as flash", "exile opponent library", "return land from
graveyard").  576 of 696 parsed loyalty abilities matched no branch at
all: the cost was paid and NOTHING resolved.  Root cause, census and the
measured A/B (Azorius Control 21.2% → 32.5%, 4/5c Control 39.3% → 50.0%
from routing one ability):
`docs/diagnostics/2026-08-30_azorius_planeswalker_loyalty_noop_root_cause.md`

Classification now happens ONCE, at DB load, in
`oracle_parser.parse_loyalty_abilities` → `CardTemplate.loyalty_abilities`
(a `{slot: LoyaltyAbility}` map).  This module only dispatches off
`LoyaltyAbility.effect_kind` and enforces legality.

Static methods; each takes `game: GameState` as its first argument.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict

from .cards import CardInstance, LoyaltyAbility, LoyaltyEffectKind
from .target_solver import enumerate_legal_targets

if TYPE_CHECKING:
    from .game_state import GameState


# The closed set of loyalty effects this engine can actually execute.
# Anything outside it is refused before the loyalty is paid.  Growing
# this set means writing the matching dispatch branch below — the census
# ratchet (`tools/check_loyalty_dispatch.py`) tracks how many printed
# abilities are still outside it, and it may only shrink.
EXECUTABLE_LOYALTY_KINDS = frozenset({
    LoyaltyEffectKind.RETURN_TO_HAND,
    LoyaltyEffectKind.DAMAGE,
    LoyaltyEffectKind.GAIN_LIFE_AND_DRAW,
    LoyaltyEffectKind.DRAW_AND_UNTAP_LANDS,
    LoyaltyEffectKind.TUCK_TARGET_INTO_LIBRARY,
    LoyaltyEffectKind.EMBLEM_EXILE_PERMANENT,
})

# CR 606: the tuck line puts the permanent into its owner's library
# "third from the top" — index 2 of a top-first library list.
_TUCK_LIBRARY_INDEX = 2

# "Draw a card, untap up to two lands" — the printed count on the
# untap rider of that family.
_DRAW_UNTAP_LAND_COUNT = 2


class PlaneswalkerManager:
    """Planeswalker loyalty-ability activation. Stateless."""

    # ─── LEGALITY ────────────────────────────────────────────────

    @staticmethod
    def loyalty_abilities(pw_card: CardInstance) -> Dict[str, LoyaltyAbility]:
        """The classified loyalty abilities of the face currently up.

        A transformed DFC uses its back face's printed lines, the same
        discriminator the rest of the engine reads.
        """
        template = pw_card.template
        if (getattr(pw_card, 'is_transformed', False)
                and template.back_face_oracle):
            return template.back_face_loyalty_abilities or {}
        return template.loyalty_abilities or {}

    @staticmethod
    def resolvable_ability_slots(pw_card: CardInstance) -> set:
        """The slots ("plus"/"zero"/"minus"/"ult") whose printed effect
        this engine can execute.

        `engine/game_runner.py` narrows the AI's menu to these before
        asking `ai.pw_ability.choose_pw_ability` to rank them: refusing
        an ability at resolution time is not enough, because the AI
        would still have spent its once-per-turn activation on it.
        """
        return {
            slot for slot, ability
            in PlaneswalkerManager.loyalty_abilities(pw_card).items()
            if ability.effect_kind in EXECUTABLE_LOYALTY_KINDS
        }

    # ─── ACTIVATION ──────────────────────────────────────────────

    @staticmethod
    def activate_planeswalker(game: "GameState", controller: int,
                              pw_card: CardInstance,
                              ability_type: str = "plus") -> bool:
        """Activate a planeswalker loyalty ability.

        Returns True when the ability was activated (loyalty paid, effect
        resolved) and False when it was REFUSED — no printed line in that
        slot, not enough loyalty, or an effect this engine cannot
        execute.  Nothing is charged on a refusal.
        """
        ability = PlaneswalkerManager.loyalty_abilities(pw_card).get(
            ability_type)
        if ability is None:
            return False

        new_loyalty = pw_card.loyalty_counters + ability.cost
        # CR 606.3 — a minus ability can only be activated with enough
        # loyalty counters to pay it.
        if new_loyalty < 0:
            return False

        # Rule 9b (activation parity): refuse BEFORE charging the cost.
        if ability.effect_kind not in EXECUTABLE_LOYALTY_KINDS:
            return False

        pw_card.loyalty_counters = new_loyalty
        pw_name = pw_card.template.name
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{pw_name} [{ability.cost:+d}] -> {ability.text}")

        PlaneswalkerManager._resolve(game, controller, pw_card, ability)
        # The walker dies at 0 loyalty — SBA 704.5p catches that.
        return True

    # ─── EFFECT RESOLUTION ───────────────────────────────────────

    @staticmethod
    def _resolve(game: "GameState", controller: int,
                 pw_card: CardInstance, ability: LoyaltyAbility) -> None:
        """Dispatch a classified loyalty effect. Every branch here has a
        `LoyaltyEffectKind` in `EXECUTABLE_LOYALTY_KINDS`; the two sets
        are pinned to each other by
        `tests/test_loyalty_effect_dispatch_census.py`."""
        kind = ability.effect_kind

        if kind is LoyaltyEffectKind.RETURN_TO_HAND:
            PlaneswalkerManager._resolve_return_to_hand(
                game, controller, pw_card, ability)
        elif kind is LoyaltyEffectKind.DAMAGE:
            PlaneswalkerManager._resolve_damage(
                game, controller, pw_card, ability)
        elif kind is LoyaltyEffectKind.GAIN_LIFE_AND_DRAW:
            PlaneswalkerManager._resolve_gain_life_and_draw(
                game, controller, pw_card, ability)
        elif kind is LoyaltyEffectKind.DRAW_AND_UNTAP_LANDS:
            PlaneswalkerManager._resolve_draw_and_untap_lands(
                game, controller)
        elif kind is LoyaltyEffectKind.TUCK_TARGET_INTO_LIBRARY:
            PlaneswalkerManager._resolve_tuck(game, controller)
        elif kind is LoyaltyEffectKind.EMBLEM_EXILE_PERMANENT:
            PlaneswalkerManager._resolve_emblem_exile(game, controller)

    @staticmethod
    def _resolve_return_to_hand(game: "GameState", controller: int,
                                pw_card: CardInstance,
                                ability: LoyaltyAbility) -> None:
        """Printed "Return [up to N] target … to its owner's / your hand."

        Targeting comes from the printed `TargetRequirement` through
        `target_solver.enumerate_legal_targets` — the same seam spell
        resolution and triggers use — and every zone change goes through
        the `zone_mgr` funnel.  "up to one" with no legal candidate
        simply returns nothing (CR 601.2c); the ability still resolved.
        """
        requirement = ability.target
        if requirement is None:
            return
        candidates = enumerate_legal_targets(
            game, controller, requirement, exclude=pw_card)

        if requirement.zone == "battlefield":
            # A printed "its owner's hand" bounce can legally target any
            # player's permanent; the controller's own board is never the
            # play, so the engine offers only the opponent's permanents —
            # the same restriction the spell-side bounce resolver applies.
            candidates = [
                c for c in candidates
                if (c.controller if c.controller is not None else c.owner)
                != controller]
            if candidates:
                from .card_effects import _nonland_permanent_threat
                opp_battlefield = game.players[1 - controller].battlefield
                best = max(candidates,
                           key=lambda c: _nonland_permanent_threat(
                               c, opp_battlefield))
                game._bounce_permanent(best)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"  returns {best.name} to its owner's hand")
        else:  # graveyard → your hand
            if candidates:
                # Recoup the largest investment — the same convention the
                # spell-side graveyard-return resolver uses.
                best = max(candidates, key=lambda c: (c.template.cmc or 0))
                game.zone_mgr.move_card(
                    game, best, "graveyard", "hand",
                    cause="returned to hand")
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"  returns {best.name} from graveyard "
                                f"to hand")

        if ability.draws:
            game.draw_cards(controller, ability.draws)

    @staticmethod
    def _resolve_damage(game: "GameState", controller: int,
                        pw_card: CardInstance,
                        ability: LoyaltyAbility) -> None:
        """Printed "<walker> deals N damage to …" — kill a creature when the
        damage is lethal to one, otherwise go face."""
        effect_desc = ability.text
        dmg_match = re.search(r'(\d+)\s+damage', effect_desc)
        if dmg_match:
            dmg = int(dmg_match.group(1))
        elif "equal to instants" in effect_desc:
            # "damage equal to the number of instants and sorceries you
            # cast this turn" — the storm-count shape.
            dmg = game._global_storm_count
        else:
            dmg = 1  # printed-but-unparsed amount: the smallest real one

        opponent = 1 - controller
        opp = game.players[opponent]
        pw_name = pw_card.template.name
        if opp.creatures:
            killable = [c for c in opp.creatures
                        if (c.toughness or 0) - c.damage_marked <= dmg]
            if killable:
                target = max(killable,
                             key=lambda c: (c.template.cmc, c.power or 0))
                target.damage_marked += dmg
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"{pw_name} deals {dmg} to {target.name}")
                if target.is_dead:
                    game._creature_dies(target)
                return
        opp.life -= dmg
        game.players[controller].damage_dealt_this_turn += dmg

    @staticmethod
    def _resolve_gain_life_and_draw(game: "GameState", controller: int,
                                    pw_card: CardInstance,
                                    ability: LoyaltyAbility) -> None:
        """Printed "You gain N life, draw N cards, …" — the value-ultimate shape.
        The "put permanents onto the battlefield" tail of that family is
        not executed."""
        effect_desc = ability.text
        life_match = re.search(r'gain\s+(\d+)\s+life', effect_desc)
        draw_match = re.search(r'draw\s+(\d+)', effect_desc)
        if life_match:
            game.gain_life(controller, int(life_match.group(1)),
                           pw_card.template.name)
        if draw_match:
            game.draw_cards(controller, int(draw_match.group(1)))

    @staticmethod
    def _resolve_draw_and_untap_lands(game: "GameState",
                                      controller: int) -> None:
        """Printed "Draw a card. Untap up to two lands."."""
        game.draw_cards(controller, 1)
        player = game.players[controller]
        untapped = 0
        for land in player.lands:
            if land.tapped and untapped < _DRAW_UNTAP_LAND_COUNT:
                land.tapped = False
                untapped += 1
        if untapped:
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"  untap {untapped} lands")

    @staticmethod
    def _resolve_tuck(game: "GameState", controller: int) -> None:
        """Printed "Put target nonland permanent into its owner's library third
        from the top." A tuck is not a death — no dies triggers — so it
        goes through the zone funnel and is then repositioned within the
        library list."""
        opp = game.players[1 - controller]
        targets = [c for c in opp.battlefield if not c.template.is_land]
        if not targets:
            return
        target = max(targets,
                     key=lambda c: (c.template.cmc or 0, c.power or 0))
        if not game.zone_mgr.move_card(
                game, target, "battlefield", "library", cause="tucked"):
            return
        # move_card appends; the printed position is third from the top.
        if target in opp.library:
            opp.library.remove(target)
            opp.library.insert(min(_TUCK_LIBRARY_INDEX, len(opp.library)),
                               target)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"  tucks {target.name} into library")

    @staticmethod
    def _resolve_emblem_exile(game: "GameState", controller: int) -> None:
        """An emblem line whose executed part exiles an opposing
        permanent. The emblem itself has no home in this engine yet, so
        only the exile resolves."""
        opp = game.players[1 - controller]
        if not opp.battlefield:
            return
        target = max(opp.battlefield,
                     key=lambda c: (c.template.cmc or 0, c.power or 0))
        game.zone_mgr.move_card(
            game, target, "battlefield", "exile", cause="emblem exile")
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"  emblem exiles {target.name}")

    # ─── ENTERS-TAPPED UNTAP TRIGGER ─────────────────────────────
