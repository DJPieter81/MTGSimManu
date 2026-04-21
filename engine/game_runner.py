"""
MTG Game Runner - v2
Orchestrates complete games and best-of-3 matches between two AI players.
Includes proper turn structure, priority passing, stack resolution,
sideboarding between games, planeswalker activation, and combo support.
"""
from __future__ import annotations
import random
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from .game_state import GameState, Phase, PHASE_ORDER, PlayerState
from .cards import CardTemplate, CardInstance, Keyword, CardType
from .card_database import CardDatabase
from .stack import StackItem
from .turn_manager import TurnManager, TurnStep
from .priority_system import PrioritySystem
from .combat_manager import CombatManager
from .callbacks import GameCallbacks

# MTG rule: "Urza's Tron" — three specific lands that produce 7 colorless
# when all three are in play. Analogous to basic land type → color mapping.
URZA_TRON_LANDS = {"Urza's Tower", "Urza's Mine", "Urza's Power Plant"}

from ai.ev_player import EVPlayer as AIPlayer
from ai.board_eval import evaluate_action, Action, ActionType
from ai.mana_planner import analyze_mana_needs, choose_fetch_target


def _sorcery_speed_only_active(player: PlayerState) -> bool:
    """True if the player controls a "cast at sorcery speed only" effect.

    Generic oracle-pattern match — handles Teferi, Time Raveler and any
    future card with the same static. Used to shut down opponent instant-
    speed response windows (counterspells, removal, evoke) during this
    player's turn.
    """
    for c in player.battlefield:
        oracle = (c.template.oracle_text or '').lower()
        if 'cast spells only any time they could cast a sorcery' in oracle:
            return True
    return False


class AICallbacks(GameCallbacks):
    """Wires engine callbacks to AI decision functions."""

    def should_pay_life_for_untapped(self, game, player_idx, land):
        """Should we pay life to enter this land untapped?

        Only pays if the extra mana enables a spell we couldn't cast
        otherwise. Raw mana advantage without spell enablement isn't
        worth life points. Derived from template.untap_life_cost.
        """
        from ai.ev_evaluator import snapshot_from_game, evaluate_board
        from ai.strategy_profile import DECK_ARCHETYPES

        player = game.players[player_idx]
        life_cost = land.template.untap_life_cost if hasattr(land, 'template') else 2

        # Hard floor: never pay life to death
        if player.life <= life_cost:
            return False

        # Fetch-shock staggering (Task 3) — decision lives in ai/mana_planner
        deck_name = player.deck_name
        arch_enum = DECK_ARCHETYPES.get(deck_name)
        archetype = arch_enum.value if arch_enum else "midrange"
        from ai.mana_planner import should_stagger_shock
        if should_stagger_shock(game, player_idx, land, archetype):
            return False

        # Determine archetype for evaluation
        deck_name = player.deck_name
        arch_enum = DECK_ARCHETYPES.get(deck_name)
        archetype = arch_enum.value if arch_enum else "midrange"

        # Combo decks: always pay life in early turns — every mana matters
        # for assembling the combo, 2 life is irrelevant
        if archetype == "combo" and game.turn_number <= 8:
            return True

        # Snapshot the current state
        snap = snapshot_from_game(game, player_idx)

        # Project "paid" state: -life_cost life, +1 untapped mana
        paid = snap.__class__(
            **{f.name: getattr(snap, f.name) for f in snap.__dataclass_fields__.values()}
        )
        paid.my_life = snap.my_life - life_cost
        paid.my_mana = snap.my_mana + 1
        paid.my_total_lands = snap.my_total_lands + 1

        # Project "tapped" state: keep life, land is tapped (no mana this turn)
        tapped = snap.__class__(
            **{f.name: getattr(snap, f.name) for f in snap.__dataclass_fields__.values()}
        )
        tapped.my_total_lands = snap.my_total_lands + 1
        # Tapped land: mana available doesn't increase this turn

        # Compare board values
        paid_value = evaluate_board(paid, archetype)
        tapped_value = evaluate_board(tapped, archetype)

        # Also factor in spell enablement: if shocking enables a spell
        # we couldn't cast otherwise, that's a significant bonus
        existing_colors = set()
        for l in player.lands:
            existing_colors.update(l.template.produces_mana)
        land_colors = set(land.template.produces_mana)
        combined = existing_colors | land_colors

        enables_spell = False
        mana_if_paid = len(player.untapped_lands) + 1
        mana_if_tapped = len(player.untapped_lands)
        for card in player.hand:
            if card.template.is_land:
                continue
            cmc = card.template.cmc or 0
            if cmc == 0 or cmc > mana_if_paid:
                continue
            if cmc > mana_if_tapped:
                mc = card.template.mana_cost
                spell_colors = set()
                for code, attr in [("W", "white"), ("U", "blue"), ("B", "black"),
                                   ("R", "red"), ("G", "green")]:
                    if getattr(mc, attr, 0) > 0:
                        spell_colors.add(code)
                if spell_colors <= combined:
                    enables_spell = True
                    break

        # Only pay life if it enables a spell we couldn't cast otherwise.
        # Raw mana advantage without spell enablement isn't worth life.
        if enables_spell:
            return True

        # No spell enabled — only pay if board eval strongly favors it
        # (e.g., we have 0-cost spells that benefit from open mana for instants)
        return paid_value > tapped_value + life_cost * 0.5

    def choose_fetch_target(self, game, player_idx, fetch_card, library, fetch_colors):
        player = game.players[player_idx]
        needs = analyze_mana_needs(game, player_idx, player.effective_cmc_overrides)
        return choose_fetch_target(
            library, fetch_colors, needs, turn=game.turn_number
        )

    def should_evoke(self, game, player_idx, card):
        return evaluate_action(
            game, player_idx, Action(ActionType.EVOKE, {'card': card})
        ) > 0

    def should_dash(self, game, player_idx, card, can_normal, can_dash):
        return evaluate_action(
            game, player_idx,
            Action(ActionType.DASH, {'card': card, 'can_normal': can_normal, 'can_dash': can_dash})
        ) > 0


@dataclass
class GameResult:
    """Result of a single game simulation."""
    winner: Optional[int]  # 0 or 1, None for draw
    winner_deck: str
    loser_deck: str
    turns: int
    winner_life: int
    loser_life: int
    win_condition: str  # "damage", "mill", "combo", "concede", "timeout"
    deck1_name: str = ""
    deck2_name: str = ""
    deck1_lands_played: int = 0
    deck2_lands_played: int = 0
    deck1_spells_cast: int = 0
    deck2_spells_cast: int = 0
    deck1_creatures_played: int = 0
    deck2_creatures_played: int = 0
    deck1_damage_dealt: int = 0
    deck2_damage_dealt: int = 0
    on_play_won: bool = False
    mulligan_count: List[int] = field(default_factory=lambda: [0, 0])
    game_log: List[str] = field(default_factory=list)
    game_number: int = 1  # 1, 2, or 3 in a match


@dataclass
class MatchResult:
    """Result of a best-of-3 match."""
    winner: Optional[int]
    winner_deck: str
    loser_deck: str
    games: List[GameResult]
    match_score: Tuple[int, int]  # (p1_wins, p2_wins)
    deck1_name: str = ""
    deck2_name: str = ""


class GameRunner:
    """Runs complete MTG games and matches between two AI players."""

    def __init__(self, card_db: CardDatabase, rng: random.Random = None):
        self.card_db = card_db
        self.rng = rng or random.Random()

    def build_deck(self, deck_list: Dict[str, int]) -> List[CardTemplate]:
        """Convert a deck list (name -> count) to a list of CardTemplates."""
        deck = []
        missing = []
        for card_name, count in deck_list.items():
            template = self.card_db.get_card(card_name)
            if template:
                for _ in range(count):
                    deck.append(template)
            else:
                missing.append(card_name)
                placeholder = self._create_placeholder(card_name)
                for _ in range(count):
                    deck.append(placeholder)
        if missing:
            print(f"WARNING: {len(missing)} cards not in database (using placeholders): {missing}")
            if len(missing) > len(deck_list) * 0.5:
                print(f"CRITICAL: Over 50% of deck is placeholders! Is ModernAtomic.json loaded? DB has {len(self.card_db.cards)} cards.")
        return deck

    def _create_placeholder(self, name: str) -> CardTemplate:
        """Create a placeholder card for cards not in the database."""
        from .mana import ManaCost
        return CardTemplate(
            name=name,
            card_types=[CardType.CREATURE],
            mana_cost=ManaCost(generic=2),
            power=2,
            toughness=2,
            tags={"creature", "placeholder"},
        )

    # ─── MATCH (BEST OF 3) ──────────────────────────────────────

    def run_match(self, deck1_name: str, deck1_data: dict,
                   deck2_name: str, deck2_data: dict,
                   verbose: bool = False) -> MatchResult:
        """Run a best-of-3 match with sideboarding between games."""
        games = []
        score = [0, 0]

        # Current mainboards (may change after sideboarding)
        d1_main = dict(deck1_data.get("mainboard", {}))
        d1_side = dict(deck1_data.get("sideboard", {}))
        d2_main = dict(deck2_data.get("mainboard", {}))
        d2_side = dict(deck2_data.get("sideboard", {}))

        for game_num in range(1, 4):
            # CR 103.2: after game 1 the loser of the previous game chooses
            # who plays first. Default = loser plays (on-play is ~54% WR).
            forced = None
            if game_num > 1 and games[-1].winner is not None:
                forced = 1 - games[-1].winner

            result = self.run_game(
                deck1_name, d1_main,
                deck2_name, d2_main,
                deck1_sideboard=d1_side,
                deck2_sideboard=d2_side,
                verbose=verbose,
                forced_first_player=forced,
            )
            result.game_number = game_num
            games.append(result)

            if result.winner is not None:
                score[result.winner] += 1

            # Check if match is decided
            if score[0] >= 2 or score[1] >= 2:
                break

            # Sideboard for next game
            if game_num < 3:
                d1_main, d1_side = self._sideboard(
                    d1_main, d1_side, deck1_name, deck2_name)
                d2_main, d2_side = self._sideboard(
                    d2_main, d2_side, deck2_name, deck1_name)

        match_winner = 0 if score[0] > score[1] else (1 if score[1] > score[0] else None)
        deck_names = [deck1_name, deck2_name]

        return MatchResult(
            winner=match_winner,
            winner_deck=deck_names[match_winner] if match_winner is not None else "draw",
            loser_deck=deck_names[1-match_winner] if match_winner is not None else "draw",
            games=games,
            match_score=tuple(score),
            deck1_name=deck1_name,
            deck2_name=deck2_name,
        )

    def _sideboard(self, mainboard: dict, sideboard_cards: dict,
                    my_deck: str, opponent_deck: str) -> Tuple[dict, dict]:
        """Delegate to sideboard_manager module."""
        from .sideboard_manager import sideboard
        return sideboard(mainboard, sideboard_cards, my_deck, opponent_deck)

    # ─── SINGLE GAME ─────────────────────────────────────────────

    def run_game(self, deck1_name: str, deck1_list: Dict[str, int],
                  deck2_name: str, deck2_list: Dict[str, int],
                  verbose: bool = False,
                  deck1_sideboard: Dict[str, int] = None,
                  deck2_sideboard: Dict[str, int] = None,
                  forced_first_player: Optional[int] = None) -> GameResult:
        """Run a complete game and return the result.

        forced_first_player: see GameState.setup_game. Bo3 match orchestration
        passes this for games 2-3 so the loser of the previous game plays first.
        """
        deck1 = self.build_deck(deck1_list)
        deck2 = self.build_deck(deck2_list)

        game = GameState(rng=self.rng, callbacks=AICallbacks())
        game.verbose = verbose
        game.setup_game(deck1, deck2, forced_first_player=forced_first_player)
        game.players[0].deck_name = deck1_name
        game.players[1].deck_name = deck2_name

        # Load sideboards for Wish effects
        if deck1_sideboard:
            for template in self.build_deck(deck1_sideboard):
                card = CardInstance(
                    template=template, owner=0, controller=0,
                    instance_id=game.next_instance_id(), zone="sideboard",
                )
                card._game_state = game
                game.players[0].sideboard.append(card)
        if deck2_sideboard:
            for template in self.build_deck(deck2_sideboard):
                card = CardInstance(
                    template=template, owner=1, controller=1,
                    instance_id=game.next_instance_id(), zone="sideboard",
                )
                card._game_state = game
                game.players[1].sideboard.append(card)

        ai1 = AIPlayer(0, deck1_name, self.rng)
        ai2 = AIPlayer(1, deck2_name, self.rng)
        ais = [ai1, ai2]

        # Copy effective CMC overrides from gameplan to PlayerState
        # so mana decisions (shock/fetch) use domain cost reduction etc.
        for p_idx, ai in enumerate(ais):
            if ai.goal_engine and ai.goal_engine.gameplan.mulligan_effective_cmc:
                game.players[p_idx].effective_cmc_overrides = dict(
                    ai.goal_engine.gameplan.mulligan_effective_cmc)

        # Compute deck composition densities for lookahead opponent modeling.
        # These let the AI estimate P(opponent holds counter/removal) from
        # the actual deck, not archetype heuristics.
        for p_idx in range(2):
            p = game.players[p_idx]
            total = len(p.library) + len(p.hand)
            if total > 0:
                all_cards = p.library + p.hand
                counters = sum(1 for c in all_cards
                               if 'counterspell' in getattr(c.template, 'tags', set()))
                removal = sum(1 for c in all_cards
                              if 'removal' in getattr(c.template, 'tags', set())
                              and 'board_wipe' not in getattr(c.template, 'tags', set()))
                # Exile-based removal ignores toughness (Leyline Binding, Prismatic
                # Ending, March, Static Prison, Solitude). Detected from oracle text.
                exile = sum(1 for c in all_cards
                            if 'removal' in getattr(c.template, 'tags', set())
                            and 'exile' in (c.template.oracle_text or '').lower())
                p.counter_density = counters / total
                p.removal_density = removal / total
                p.exile_density = exile / total

        # Log die roll
        on_play = game.active_player
        on_draw = 1 - on_play
        game.log.append(f'╔══ PRE-GAME ══════════════════════════════════════════════')
        game.log.append(f'║ Die Roll: {game.players[on_play].deck_name} wins → chooses to play first')
        game.log.append(f'║ P1 (on play): {game.players[on_play].deck_name}')
        game.log.append(f'║ P2 (on draw): {game.players[on_draw].deck_name}')
        game.log.append(f'╚{"═" * 55}')

        # Mulligan phase
        mulligan_counts = [0, 0]
        for p_idx in range(2):
            hand_size = 7
            player = game.players[p_idx]
            opening = [c.name for c in player.hand]
            lands = sum(1 for c in player.hand if c.template.is_land)
            spells = hand_size - lands
            game.log.append(f'')
            game.log.append(f'P{p_idx+1} ({player.deck_name}) opening hand ({lands} lands, {spells} spells):')
            for c in player.hand:
                cmc = c.template.cmc or 0
                card_type = 'Land' if c.template.is_land else ('Creature' if c.template.is_creature else 'Spell')
                game.log.append(f'  • {c.name} [{card_type}, CMC {cmc}]')
            while hand_size >= 5:
                ai = ais[p_idx]
                keep = ai.decide_mulligan(player.hand, hand_size)
                reason = getattr(ai, 'mulligan_reason', '')
                if keep:
                    if mulligan_counts[p_idx] > 0:
                        to_bottom = ai.choose_cards_to_bottom(
                            player.hand, mulligan_counts[p_idx])
                        bottom_names = [c.name for c in to_bottom]
                        for card in to_bottom:
                            player.hand.remove(card)
                            card.zone = "library"
                            player.library.append(card)
                        kept = [c.name for c in player.hand]
                        game.log.append(
                            f"→ P{p_idx+1} mulligans to {hand_size}, "
                            f"bottoms: {bottom_names}")
                        game.log.append(f"  Keeps: {kept}")
                    else:
                        game.log.append(f"→ P{p_idx+1} KEEPS {hand_size} — {reason}")
                    break
                else:
                    mulligan_counts[p_idx] += 1
                    game.log.append(f"→ P{p_idx+1} MULLIGANS ({reason})")
                    for card in player.hand[:]:
                        player.hand.remove(card)
                        card.zone = "library"
                        player.library.append(card)
                    self.rng.shuffle(player.library)
                    game.draw_cards(p_idx, 7)
                    # Show new hand
                    lands = sum(1 for c in player.hand if c.template.is_land)
                    spells = 7 - lands
                    game.log.append(f'  New hand ({lands} lands, {spells} spells): {[c.name for c in player.hand]}')
                    hand_size -= 1
        game.log.append('')

        # Leyline mechanic: cards with "begin the game with it on the
        # battlefield" start in play if they're in the opening hand.
        for p_idx in range(2):
            player = game.players[p_idx]
            leylines = [c for c in player.hand
                        if c.template.oracle_text
                        and "begin the game with it on the battlefield"
                        in (c.template.oracle_text or "").lower()]
            for card in leylines:
                player.hand.remove(card)
                card.zone = "battlefield"
                card.tapped = False
                card.summoning_sick = False
                player.battlefield.append(card)
                # Trigger ETB effects (Leyline of the Guildpact domain setup, etc.)
                game.trigger_etb(card, p_idx)
                game.log.append(
                    f"T0 P{p_idx+1}: {card.name} begins the game on the "
                    f"battlefield (leyline)")

        stats = {
            "lands_played": [0, 0],
            "spells_cast": [0, 0],
            "creatures_played": [0, 0],
            "damage_dealt": [0, 0],
        }

        first_player = game.active_player
        turn_mgr = game.turn_mgr
        turn_mgr.first_player = first_player

        # Game loop — driven by TurnManager
        import time as _time
        _game_start = _time.monotonic()
        from ai.constants import GAME_TIMEOUT_SECONDS
        _max_game_time = GAME_TIMEOUT_SECONDS
        game._game_deadline = _game_start + _max_game_time
        while not game.game_over and game.turn_number < game.max_turns:
            if _time.monotonic() > game._game_deadline:
                break
            active = game.active_player
            ai = ais[active]
            opponent_idx = 1 - active
            opponent_ai = ais[opponent_idx]

            combat_mgr = CombatManager()

            # ── Verbose helpers (defined once per turn) ──
            def _vlog(msg):
                if getattr(game, 'verbose', False):
                    game.log.append(msg)

            for step in turn_mgr.iterate_turn(game):
                if game.game_over:
                    break
                if _time.monotonic() > game._game_deadline:
                    game.game_over = True
                    break

                def _board_summary():
                    """Emit full board state summary."""
                    p = game.players[active]
                    o = game.players[1 - active]
                    p_name = p.deck_name or f'P{active+1}'
                    o_name = o.deck_name or f'P{1-active+1}'
                    _vlog('')
                    _vlog(f'╔══ TURN {game.display_turn} — {p_name} (P{active+1}) ══════════════════════════')
                    _vlog(f'║ Life: {p_name} {p.life}  |  {o_name} {o.life}')
                    _vlog(f'║ Hand: {len(p.hand)} cards  |  Opp hand: {len(o.hand)} cards')
                    _vlog(f'║ Lands: {len(p.lands)}  |  Opp lands: {len(o.lands)}')
                    _vlog(f'║ Library: {len(p.library)}  |  Graveyard: {len(p.graveyard)}')
                    # Board creatures
                    for pidx, pobj, label in [(active, p, p_name), (1-active, o, o_name)]:
                        creatures = [f'{c.name} ({c.power}/{c.toughness})'
                                     + (' [tapped]' if c.tapped else '')
                                     for c in pobj.creatures]
                        nonc = [c.name for c in pobj.battlefield
                                if not c.template.is_land and not c.template.is_creature]
                        lands = [c.name + (' [T]' if c.tapped else '')
                                 for c in pobj.lands]
                        _vlog(f'║ {label} board:')
                        _vlog(f'║   Creatures: {", ".join(creatures) if creatures else "(empty)"}')
                        if nonc:
                            _vlog(f'║   Other: {", ".join(nonc)}')
                        _vlog(f'║   Lands: {", ".join(lands) if lands else "(none)"}')
                    _vlog(f'╚{"═" * 55}')

                if step == TurnStep.UNTAP:
                    game.current_phase = Phase.UNTAP
                    game.untap_step(active)
                    _board_summary()
                    _vlog(f'  [Untap] P{active+1} untaps all permanents')
                    # Reset planeswalker activation tracking for this turn
                    ai._pw_activated_this_turn.clear()

                elif step == TurnStep.UPKEEP:
                    game.current_phase = Phase.UPKEEP
                    _vlog(f'  [Upkeep]')
                    # Rebound: cast exiled rebound spells for free
                    if hasattr(game, '_rebound_cards'):
                        to_cast = [c for c in game._rebound_cards
                                   if getattr(c, '_rebound_controller', -1) == active]
                        for rc in to_cast:
                            game._rebound_cards.remove(rc)
                            if rc in game.players[active].exile:
                                game.players[active].exile.remove(rc)
                            # Gate rebound on valid targets (avoids wasted fizzles)
                            tags = getattr(rc.template, 'tags', set())
                            player = game.players[active]
                            opponent = game.players[1 - active]
                            skip = False
                            if 'blink' in tags and not player.creatures:
                                skip = True  # no creature to blink
                            elif ('removal' in tags and 'board_wipe' not in tags
                                  and not opponent.creatures):
                                skip = True  # no creature to target
                            if skip:
                                game.log.append(f"T{game.display_turn} P{active+1}: "
                                                f"Rebound {rc.name} skipped (no valid target)")
                                continue
                            rc._free_cast_opportunity = True  # rebound: free cast
                            game.cast_spell(active, rc, free_cast=True)
                            game.log.append(f"T{game.display_turn} P{active+1}: "
                                            f"Rebound {rc.name}")
                    # Urza's Saga chapter triggers
                    self._process_saga_chapters(game, active)
                    # Activated abilities fired on our upkeep (Isochron Scepter, etc.)
                    self._process_upkeep_activations(game, active)
                    game.process_triggers()
                    self._resolve_stack_loop(game)

                elif step == TurnStep.DRAW:
                    game.current_phase = Phase.DRAW
                    if not turn_mgr.should_skip_draw(game):
                        drawn = game.draw_cards(active, 1)
                        if drawn and getattr(game, 'verbose', False):
                            card_name = drawn[0].name if drawn else '?'
                            _vlog(f'  [Draw] P{active+1} draws: {card_name}')
                    else:
                        _vlog(f'  [Draw] Skipped (first turn on play)')

                elif step == TurnStep.MAIN1:
                    game.current_phase = Phase.MAIN1
                    _vlog(f'  [Main 1]')
                    prev_lands = len(game.players[active].lands)
                    self._execute_main_phase(game, ai, opponent_ai)
                    if game.game_over:
                        break
                    new_lands = len(game.players[active].lands) - prev_lands
                    stats["lands_played"][active] += max(0, new_lands)
                    self._activate_planeswalkers(game, ai)
                    if game.game_over:
                        break
                    self._activate_tap_abilities(game, active)
                    if game.game_over:
                        break
                    self._activate_griselbrand(game, active)
                    if game.game_over:
                        break
                    self._activate_sacrifice_abilities(game, active)

                elif step == TurnStep.BEGIN_COMBAT:
                    game.current_phase = Phase.BEGIN_COMBAT
                    _vlog(f'  [Begin Combat]')
                    for p in game.players:
                        p.mana_pool.empty()
                    self._opponent_instant_window(game, opponent_ai, ai)

                elif step == TurnStep.DECLARE_ATTACKERS:
                    game.current_phase = Phase.DECLARE_ATTACKERS
                    attackers = ai.decide_attackers(game)
                    if attackers:
                        atk_names = [a.name for a in attackers]
                        _vlog(f'  [Declare Attackers] P{active+1} attacks with: {", ".join(atk_names)}')
                        combat_mgr.declare_attackers(game, attackers, active)
                    else:
                        _vlog(f'  [Declare Attackers] P{active+1} does not attack')

                elif step == TurnStep.AFTER_ATTACKERS_DECLARED:
                    if combat_mgr.attackers:
                        # Priority window after attackers declared
                        game.process_triggers()
                        self._resolve_stack_loop(game)

                elif step == TurnStep.DECLARE_BLOCKERS:
                    if combat_mgr.attackers:
                        game.current_phase = Phase.DECLARE_BLOCKERS
                        blocks = opponent_ai.decide_blockers(game, combat_mgr.attackers)
                        if blocks:
                            _vlog(f'  [Declare Blockers] P{1-active+1} blocks: (see BLOCK lines below)')
                        else:
                            _vlog(f'  [Declare Blockers] P{1-active+1} does not block')
                        combat_mgr.declare_blockers(game, blocks)

                elif step == TurnStep.AFTER_BLOCKERS_DECLARED:
                    pass  # Future: priority window after blockers

                elif step == TurnStep.FIRST_STRIKE_DAMAGE:
                    pass  # Handled inside game.combat_damage()

                elif step == TurnStep.COMBAT_DAMAGE:
                    if combat_mgr.attackers:
                        game.current_phase = Phase.COMBAT_DAMAGE
                        pre_life = game.players[opponent_idx].life
                        combat_mgr.resolve_combat_damage(game)
                        damage = pre_life - game.players[opponent_idx].life
                        stats["damage_dealt"][active] += max(0, damage)
                        if damage > 0:
                            _vlog(f'  [Combat Damage] {damage} damage dealt → '
                                  f'P{opponent_idx+1} life: {pre_life} → {game.players[opponent_idx].life}')
                        if game.game_over:
                            break

                elif step == TurnStep.END_COMBAT:
                    game.current_phase = Phase.END_COMBAT
                    combat_mgr.end_combat(game)
                    _vlog(f'  [End Combat]')

                elif step == TurnStep.MAIN2:
                    game.current_phase = Phase.MAIN2
                    _vlog(f'  [Main 2]')
                    for p in game.players:
                        p.mana_pool.empty()
                    self._execute_main_phase(game, ai, opponent_ai)
                    if game.game_over:
                        break
                    self._activate_planeswalkers(game, ai)
                    if game.game_over:
                        break
                    self._activate_tap_abilities(game, active)
                    if game.game_over:
                        break
                    # Griselbrand activation in MAIN2 (in case reanimated during MAIN2)
                    self._activate_griselbrand(game, active)
                    if game.game_over:
                        break
                    stats["spells_cast"][active] += game.players[active].spells_cast_this_turn

                elif step == TurnStep.END_STEP:
                    game.current_phase = Phase.END_STEP
                    _vlog(f'  [End Step]')
                    # Goblin Bombardment: sacrifice tokens/small creatures to deal damage
                    self._activate_goblin_bombardment(game, active)
                    if game.game_over:
                        break
                    # Activated artifacts: Expedition Map, Ratchet Bomb
                    self._activate_utility_artifacts(game, active)
                    # Phelia blink returns: exiled permanents come back + trigger ETB
                    if hasattr(game, '_phelia_returns') and game._phelia_returns:
                        from engine.card_effects import EFFECT_REGISTRY, EffectTiming
                        for perm in game.players[active].battlefield:
                            # Generic: any creature with end-step return trigger
                            p_oracle = (perm.template.oracle_text or '').lower()
                            if ('end step' in p_oracle and 'return' in p_oracle
                                    and 'exiled' in p_oracle):
                                EFFECT_REGISTRY.execute(
                                    perm.name, EffectTiming.END_STEP,
                                    game, perm, active)
                                break
                    # End-step instant window: opponent can cast instants/flash
                    self._end_step_instant_window(game, opponent_ai, ai)
                    if game.game_over:
                        break
                    game.end_of_turn_cleanup()
                    game.process_triggers()
                    self._resolve_stack_loop(game)

                elif step == TurnStep.CLEANUP:
                    game.current_phase = Phase.CLEANUP
                    game.cleanup_step()
                    # Discard to hand size
                    p = game.players[active]
                    if len(p.hand) > 7 and getattr(game, 'verbose', False):
                        _vlog(f'  [Cleanup] P{active+1} discards to hand size 7')

            if game.game_over:
                break

            # Switch active player
            game.switch_active_player()

        # Determine result
        if game.game_over and game.winner is not None:
            winner = game.winner
            loser = 1 - winner
            win_condition = "damage"
            if game.players[loser].life > 0:
                if not game.players[loser].library:
                    win_condition = "mill"
                else:
                    win_condition = "combo"
        elif game.turn_number >= game.max_turns:
            if game.players[0].life > game.players[1].life:
                winner = 0
                loser = 1
            elif game.players[1].life > game.players[0].life:
                winner = 1
                loser = 0
            else:
                winner = None
                loser = None
            win_condition = "timeout"
        else:
            winner = None
            loser = None
            win_condition = "draw"

        deck_names = [deck1_name, deck2_name]

        result = GameResult(
            winner=winner,
            winner_deck=deck_names[winner] if winner is not None else "draw",
            loser_deck=deck_names[loser] if loser is not None else "draw",
            turns=int(game.display_turn),
            winner_life=game.players[winner].life if winner is not None else 0,
            loser_life=game.players[loser].life if loser is not None else 0,
            win_condition=win_condition,
            deck1_name=deck1_name,
            deck2_name=deck2_name,
            deck1_lands_played=stats["lands_played"][0],
            deck2_lands_played=stats["lands_played"][1],
            deck1_spells_cast=stats["spells_cast"][0],
            deck2_spells_cast=stats["spells_cast"][1],
            deck1_damage_dealt=stats["damage_dealt"][0],
            deck2_damage_dealt=stats["damage_dealt"][1],
            on_play_won=(winner == first_player) if winner is not None else False,
            mulligan_count=mulligan_counts,
            game_log=game.log if verbose else [],
        )

        return result

    def _resolve_stack_loop(self, game: GameState):
        """Resolve all items on the stack, checking SBAs after each.

        Per CR 117.4: If all players pass in succession and the stack
        is not empty, the top item resolves. Then SBAs are checked,
        triggers are put on the stack, and the active player gets priority.
        """
        import time as _time
        _max_resolves = 100  # safety valve
        _resolves = 0
        while not game.stack.is_empty and _resolves < _max_resolves:
            if hasattr(game, '_game_deadline') and _time.monotonic() > game._game_deadline:
                game.stack.items.clear()
                return
            game.resolve_stack()
            game.check_state_based_actions()
            _resolves += 1
            if game.game_over:
                return

    def _opponent_instant_window(self, game: GameState, opponent_ai: AIPlayer,
                                   active_ai: AIPlayer):
        """Give the opponent a window to cast instant-speed spells before combat.
        
        Pre-combat is the best time to remove creatures with haste or
        high-value attackers before they deal damage.
        """
        self._cast_instant_removal(game, opponent_ai, active_ai,
                                   context="pre_combat", max_instants=3)

    def _end_step_instant_window(self, game: GameState, opponent_ai: AIPlayer,
                                  active_ai: AIPlayer):
        """Give the opponent a window to cast instant-speed spells at end of turn.
        
        End step is ideal for:
        - Removal that we held up mana for during the turn
        - Flash creatures (Endurance, Solitude evoke, etc.)
        - Using mana efficiently before it empties
        """
        self._cast_instant_removal(game, opponent_ai, active_ai,
                                   context="end_step", max_instants=3)

    def _cast_instant_removal(self, game: GameState, opponent_ai: AIPlayer,
                               active_ai: AIPlayer, context: str = "pre_combat",
                               max_instants: int = 3):
        """Unified instant-speed interaction window.

        Uses threat assessment to decide which creatures to remove and
        whether to spend resources now or hold them.

        v2: Fixed bug where active_player.creatures was checked instead of
        the active player's board. Now properly evaluates the active player's
        creatures as threats to remove. Lowered thresholds so removal fires
        more aggressively against value engines and early threats.

        Teferi gate: if the ACTIVE player controls a "cast at sorcery speed
        only" permanent (Teferi, Time Raveler et al.), opponents cannot
        cast anything at instant speed during this window. Detect via oracle
        pattern and bail out early.
        """
        from ai.evaluator import _permanent_value

        opponent_idx = opponent_ai.player_idx
        opponent = game.players[opponent_idx]  # the player holding removal
        active_player = game.players[active_ai.player_idx]  # the player whose turn it is
        if _sorcery_speed_only_active(active_player):
            return

        cast_count = 0

        # Sort hand by removal priority: prioritize cheap efficient removal
        instant_removal = []
        flash_creatures = []
        evoke_creatures = []
        for card in list(opponent.hand):
            if not game.can_cast(opponent_idx, card):
                continue
            if card.template.is_instant or card.template.has_flash:
                if "removal" in card.template.tags:
                    instant_removal.append(card)
                    # Flash creatures with removal (e.g. Bowmasters) are also
                    # valuable as bodies — add to flash_creatures as fallback
                    # in case removal targeting fails
                    if card.template.is_creature and card.template.has_flash:
                        flash_creatures.append(card)
                elif card.template.is_creature and card.template.has_flash:
                    flash_creatures.append(card)
            # Evoke creatures (Solitude, Endurance, Subtlety) can be cast at instant speed
            elif card.template.is_creature and "evoke" in card.template.tags:
                if "removal" in card.template.tags:
                    instant_removal.append(card)
                else:
                    flash_creatures.append(card)

        # Assess threat level of the ACTIVE player's board (the one we want to remove)
        if active_player.creatures:
            threat_values = [
                (c, _permanent_value(c, active_player, game, active_ai.player_idx))
                for c in active_player.creatures
            ]
            threat_values.sort(key=lambda tv: tv[1], reverse=True)
            max_threat = threat_values[0][1]
            total_threat = sum(tv[1] for tv in threat_values)
        else:
            max_threat = 0
            total_threat = 0

        # Threat thresholds — much more aggressive than v1
        # Pre-combat: remove creatures that are about to attack (lower threshold)
        # End-step: use remaining mana on value engines
        if context == "pre_combat":
            threat_threshold = 2.0  # was 3.0 — now catches 2-power creatures with keywords
        else:
            threat_threshold = 2.5  # was 4.0 — now catches value engines at end step

        # Additional urgency: if total board threat is high, lower threshold further
        if total_threat >= 15.0:
            threat_threshold = max(1.0, threat_threshold - 1.0)

        # Cast removal on high-threat targets
        if max_threat >= threat_threshold and instant_removal:
            # Sort removal: prefer cheap removal for expensive threats (tempo)
            instant_removal.sort(key=lambda c: c.template.cmc or 0)

            for card in instant_removal:
                if cast_count >= max_instants:
                    break
                if not active_player.creatures:
                    break
                if not game.can_cast(opponent_idx, card):
                    continue

                # Choose the highest-threat target
                targets = opponent_ai._choose_targets(game, card)
                if targets:
                    success = game.cast_spell(opponent_idx, card, targets)
                    if success:
                        while not game.stack.is_empty:
                            game.resolve_stack()
                            game.check_state_based_actions()
                            if game.game_over:
                                return
                        cast_count += 1

        # End-step only: deploy flash creatures if we have unused mana
        if context == "end_step" and flash_creatures and cast_count < max_instants:
            for card in flash_creatures:
                if cast_count >= max_instants:
                    break
                # Skip if already cast as removal above
                if card.zone != "hand":
                    continue
                if not game.can_cast(opponent_idx, card):
                    continue
                success = game.cast_spell(opponent_idx, card, [])
                if success:
                    while not game.stack.is_empty:
                        game.resolve_stack()
                        game.check_state_based_actions()
                        if game.game_over:
                            return
                    cast_count += 1

    def _execute_main_phase(self, game: GameState, ai: AIPlayer,
                             opponent_ai: AIPlayer):
        """Execute a main phase with priority-based stack interaction.
        """
        import time as _time
        if hasattr(game, '_game_deadline') and _time.monotonic() > game._game_deadline:
            return
        # Per CR 117.3a: Active player receives priority at the beginning
        # of the main phase. They can play lands, cast spells, or pass.
        # After each spell, opponent gets a response window (CR 117.3d).
        # Both must pass for the stack to resolve (CR 117.4).
        priority = game.priority
        # Combo decks (Storm, Living End) need to chain many spells in one turn.
        # Storm: 10+ rituals + cantrips + finisher = 15-25 actions
        # Living End: cycling + cascade = 5-10 actions
        # Combo decks may chain many spells per turn (storm, cascade).
        from ai.constants import MAX_ACTIONS_COMBO, MAX_ACTIONS_NORMAL
        from ai.strategy_profile import DECK_ARCHETYPES, ArchetypeStrategy
        deck_name = game.players[ai.player_idx].deck_name
        arch = DECK_ARCHETYPES.get(deck_name)
        is_combo = arch == ArchetypeStrategy.COMBO if arch else False
        max_actions = MAX_ACTIONS_COMBO if is_combo else MAX_ACTIONS_NORMAL
        actions = 0
        _last_failed_card = None  # Track failed casts to prevent infinite loops
        _consecutive_fails = 0

        while actions < max_actions and not game.game_over:
            if hasattr(game, '_game_deadline') and _time.monotonic() > game._game_deadline:
                return
            # Active player gets priority (CR 117.3a)
            priority.give_priority(game, ai.player_idx)

            decision = ai.decide_main_phase(game)
            if decision is None:
                # Active player passes priority
                break

            action, card, targets = decision
            priority.take_action(game)  # CR 117.3c

            if action == "play_land":
                game.play_land(ai.player_idx, card)
            elif action == "cycle":
                game.activate_cycling(ai.player_idx, card)
            elif action == "equip":
                # targets[0] = creature instance_id to equip to
                if targets:
                    creature = game.get_card_by_id(targets[0])
                    if creature:
                        game.equip_creature(ai.player_idx, card, creature)
            elif action == "cast_spell":
                # Tag with current goal and card role for detailed logging
                if getattr(game, 'verbose', False) and hasattr(ai, 'goal_engine') and ai.goal_engine:
                    ge = ai.goal_engine
                    gp = ge.gameplan
                    goal_name = gp.goals[ge.current_goal_idx].goal_type.value if gp and gp.goals and ge.current_goal_idx < len(gp.goals) else '?'
                    card_role = None
                    if gp and gp.goals and ge.current_goal_idx < len(gp.goals):
                        for role, names in gp.goals[ge.current_goal_idx].card_roles.items():
                            if card.name in names:
                                card_role = role
                                break
                    role_str = f' [{card_role}]' if card_role else ''
                    game.log.append(f'    → Goal: {goal_name}{role_str}')
                # Log target reasoning if available
                if getattr(game, 'verbose', False):
                    _tgt = getattr(ai, '_last_played_target_reason', '')
                    if _tgt:
                        game.log.append(f'    [Target] {_tgt}')
                        ai._last_played_target_reason = ''
                success = game.cast_spell(ai.player_idx, card, targets)
                if not success:
                    # Track failed casts to prevent infinite loops.
                    # Break immediately if the same card name fails twice
                    # (first attempt + one retry).
                    if _last_failed_card and card.name == _last_failed_card.name:
                        break  # Same card failing again — stop
                    else:
                        _last_failed_card = card
                if success:
                    _last_failed_card = None
                    _consecutive_fails = 0
                    # Opponent gets priority to respond (CR 117.3d) — UNLESS
                    # the active player controls a Teferi-style "cast at
                    # sorcery speed only" effect. In that case, the opponent
                    # literally cannot respond at instant speed.
                    active_player = game.players[ai.player_idx]
                    if _sorcery_speed_only_active(active_player):
                        pass  # skip response window entirely
                    elif not game.stack.is_empty:
                        top = game.stack.top
                        if top:
                            priority.give_priority(game, opponent_ai.player_idx)
                            response = opponent_ai.decide_response(game, top)
                            if response:
                                resp_card, resp_targets = response
                                if getattr(game, 'verbose', False):
                                    game.log.append(f'    [Priority] P{opponent_ai.player_idx+1} responds with {resp_card.name}')
                                game.cast_spell(opponent_ai.player_idx,
                                                resp_card, resp_targets)
                                priority.take_action(game)
                                if hasattr(ai, 'bhi'):
                                    ai.bhi.observe_spell_cast(
                                        game, getattr(resp_card.template, 'tags', set()))
                            else:
                                if getattr(game, 'verbose', False):
                                    game.log.append(f'    [Priority] P{opponent_ai.player_idx+1} passes (no response)')
                                if hasattr(ai, 'bhi'):
                                    opp = game.players[opponent_ai.player_idx]
                                    opp_mana = len(opp.untapped_lands) + opp.mana_pool.total()
                                    spell_template = top.source.template if top.source else None
                                    ai.bhi.observe_priority_pass(
                                        game,
                                        spell_on_stack=True,
                                        spell_is_creature=spell_template.is_creature if spell_template else False,
                                        opp_mana_available=opp_mana)

                    # Both passed — resolve stack (CR 117.4)
                    self._resolve_stack_loop(game)

            actions += 1
            game.check_state_based_actions()
            if game.game_over:
                return

    def _activate_planeswalkers(self, game: GameState, ai: AIPlayer):
        """Activate planeswalker loyalty abilities for the active player."""
        active = ai.player_idx
        player = game.players[active]
        opponent = 1 - active

        for pw in player.planeswalkers:
            if pw.entered_battlefield_this_turn and pw.loyalty_counters <= 0:
                continue

            # Planeswalkers can only activate ONE loyalty ability per turn
            if pw.instance_id in ai._pw_activated_this_turn:
                continue

            pw_name = pw.template.name
            from .game_state import _parse_planeswalker_abilities
            # Use back face oracle for transformed cards (e.g., Ral creature → PW)
            oracle = pw.template.oracle_text
            loyalty = pw.template.loyalty
            if getattr(pw, 'is_transformed', False) and pw.template.back_face_oracle:
                oracle = pw.template.back_face_oracle
                loyalty = pw.template.back_face_loyalty
            pw_data = _parse_planeswalker_abilities(oracle, loyalty)
            if not pw_data.get("plus") and not pw_data.get("minus"):
                continue  # no parseable abilities
            opp = game.players[opponent]

            ability_type = self._choose_pw_ability(pw, pw_name, pw_data, player, opp, game)

            game.activate_planeswalker(active, pw, ability_type)
            ai._pw_activated_this_turn.add(pw.instance_id)
            game.check_state_based_actions()
            if game.game_over:
                return

    def _choose_pw_ability(self, pw, pw_name, pw_data, player, opp, game):
        """Choose the best planeswalker ability to activate.

        Uses ability descriptions to make generic decisions rather than
        hardcoding per-card logic. Any planeswalker with standard loyalty
        ability oracle text will automatically get reasonable behavior.
        """
        def can_afford(ability_key):
            if ability_key not in pw_data:
                return False
            cost, _ = pw_data[ability_key]
            return pw.loyalty_counters + cost >= 0

        def desc(ability_key):
            if ability_key not in pw_data:
                return ""
            return pw_data[ability_key][1].lower()

        def loyalty_after(ability_key):
            if ability_key not in pw_data:
                return pw.loyalty_counters
            cost, _ = pw_data[ability_key]
            return pw.loyalty_counters + cost

        # Always ult if we can (game-winning)
        if can_afford("ult"):
            return "ult"

        # Collect all non-ult abilities we can afford
        abilities = []  # (key, cost, desc)
        for key in ["plus", "zero", "minus"]:
            if can_afford(key):
                abilities.append((key, pw_data[key][0], desc(key)))

        if not abilities:
            return "plus"  # shouldn't happen, but safe fallback

        # ── Evaluate each ability by description patterns ──
        best_key = "plus"
        best_score = -100

        for key, cost, ability_desc in abilities:
            score = 0
            remaining_loyalty = loyalty_after(key)

            # DAMAGE abilities: "deal X damage" or "deals X damage"
            if "damage" in ability_desc or "deals" in ability_desc:
                # Can we kill an opponent creature?
                if opp.creatures:
                    # Parse damage amount from description
                    dmg = 1
                    for word in ability_desc.split():
                        if word.isdigit():
                            dmg = int(word)
                            break
                    killable = [c for c in opp.creatures
                                if (c.toughness or 0) - c.damage_marked <= dmg]
                    if killable:
                        best_kill = max(killable, key=lambda c: c.template.cmc)
                        score = 20 + best_kill.template.cmc  # high priority: kill creatures
                    else:
                        # No killable creatures, but can still go face
                        if remaining_loyalty >= 2:  # don't suicide the PW
                            score = 3 + dmg  # low-priority chip damage
                        else:
                            score = -5  # too risky
                elif remaining_loyalty >= 2:
                    score = 3  # ping face when no creatures

            # BOUNCE abilities: "return" + "to" + "hand" or "bounce"
            elif "bounce" in ability_desc or ("return" in ability_desc and "hand" in ability_desc and "owner" in ability_desc):
                nonlands = [c for c in opp.battlefield if not c.template.is_land]
                if nonlands:
                    best_cmc = max(c.template.cmc for c in nonlands)
                    if best_cmc >= 2:
                        score = 15 + best_cmc  # bounce high-value targets
                    else:
                        score = 2  # not worth bouncing 1-drops usually
                # Bonus if it also draws a card
                if "draw" in ability_desc:
                    score += 5

            # LAND RECURSION: "return" + "land" + "graveyard" / "hand"
            elif "land" in ability_desc and ("graveyard" in ability_desc or "return" in ability_desc):
                lands_in_gy = [c for c in player.graveyard if c.template.is_land]
                if lands_in_gy:
                    score = 12  # good value: recur fetchlands
                else:
                    score = -2  # no lands to return

            # DRAW / CARD SELECTION: "draw" or "brainstorm" or "look at"
            elif "draw" in ability_desc or "brainstorm" in ability_desc or "look at" in ability_desc:
                score = 14  # card advantage is almost always good

            # COST REDUCTION / RAMP: "cost" + "less" or "add" + mana
            elif "cost" in ability_desc and "less" in ability_desc:
                score = 8  # ramp for future turns
            elif "add" in ability_desc and any(c in ability_desc for c in "wubrgc"):
                score = 7  # mana production

            # BOARD WIPE: "exile" + "each" or "all" or "destroy all"
            elif ("exile" in ability_desc or "destroy" in ability_desc) and ("each" in ability_desc or "all" in ability_desc):
                opp_permanents = [c for c in opp.battlefield if not c.template.is_land]
                if len(opp_permanents) >= 3:
                    score = 25  # wipe when behind on board
                elif len(opp_permanents) >= 1:
                    score = 10
                else:
                    score = -5  # don't wipe an empty board

            # FLASH / TIMING: "flash" or "as though" + "flash"
            elif "flash" in ability_desc or "any time" in ability_desc:
                score = 5  # minor utility, builds loyalty

            # FATESEAL / LIBRARY MANIPULATION: "look at the top"
            elif "look at the top" in ability_desc or "fateseal" in ability_desc:
                score = 4  # minor disruption

            # Unknown ability: default to loyalty-positive
            else:
                score = 1 if cost >= 0 else -1

            # Tiebreaker: prefer loyalty-positive abilities when scores are close
            if cost > 0:
                score += 0.5  # slight bonus for building loyalty

            if score > best_score:
                best_score = score
                best_key = key

        return best_key

    def _activate_pay_life_draw(self, game: GameState, active: int):
        """Activate pay-life-draw abilities on creatures (e.g., Griselbrand).
        Detected from the 'pay_life_draw' and 'pay_life_cost_N' tags parsed
        from oracle text. Draws aggressively since these creatures are often
        temporary (e.g., Goryo's Vengeance exiles at end of turn)."""
        player = game.players[active]
        for creature in player.creatures:
            if "pay_life_draw" not in creature.template.tags:
                continue
            # CRITICAL FIX: Street Wraith and other cycling creatures have
            # pay_life_draw tagged from their cycling cost, but cycling only
            # works from hand, NOT from the battlefield. Skip creatures whose
            # pay-life-draw comes from cycling (they have the 'cycling' tag).
            if "cycling" in creature.template.tags:
                continue
            # Extract life cost from tags (e.g., 'pay_life_cost_7' -> 7)
            life_cost = 7  # default
            for tag in creature.template.tags:
                if tag.startswith("pay_life_cost_"):
                    try:
                        life_cost = int(tag.split("_")[-1])
                    except ValueError:
                        pass
                    break
            # Extract draw count from tags (e.g., 'pay_life_draw_count_7' -> 7)
            draw_count = 1  # default: most pay-life-draw abilities draw 1
            for tag in creature.template.tags:
                if tag.startswith("pay_life_draw_count_"):
                    try:
                        draw_count = int(tag.split("_")[-1])
                    except ValueError:
                        pass
                    break
            # Smart draw: consider opponent's board when deciding how aggressively to draw.
            # Griselbrand is often temporary (Goryo's exiles at EOT), so we want to draw
            # enough cards to find protection/combo pieces, but not suicide.
            opp = game.players[1 - active]
            opp_power = sum(c.power or 0 for c in opp.creatures)
            # Keep enough life to survive opponent's next attack + some buffer
            safe_life = max(opp_power + 3, life_cost + 1)  # at least survive one attack
            # But if we have very few cards, be more aggressive (need to find answers)
            if len(player.hand) <= 2:
                safe_life = max(life_cost + 1, opp_power)  # more aggressive when desperate
            min_life = safe_life
            max_activations = 3  # Griselbrand typically activates 1-2 times
            activations = 0
            while player.life >= min_life and len(player.hand) < 14 and activations < max_activations:
                player.life -= life_cost
                game.draw_cards(active, draw_count)
                game.log.append(f"T{game.display_turn} P{active+1}: "
                                f"{creature.name}: pay {life_cost} life, draw {draw_count}")
                activations += 1
                if game.game_over:
                    return
            break  # only activate one such creature per call

    def _process_saga_chapters(self, game: GameState, active: int):
        """Process saga chapter triggers during upkeep.

        Each saga gains a lore counter per turn (starting the turn after ETB).
        Supported sagas: Urza's Saga, The Legend of Roku.
        """
        from engine.cards import CardType, Supertype
        player = game.players[active]
        sagas_to_sacrifice = []
        sagas_to_transform = []
        for card in list(player.battlefield):
            is_saga = 'Saga' in (card.template.subtypes or [])
            if not is_saga:
                continue
            # Initialize lore counter on first upkeep
            if not hasattr(card, 'other_counters') or card.other_counters is None:
                card.other_counters = {}
            lore = card.other_counters.get('lore', 0)
            # Saga entered this turn — skip first upkeep (it gets chapter I on ETB)
            if lore == 0:
                card.other_counters['lore'] = 1
                continue
            lore += 1
            card.other_counters['lore'] = lore

            card_oracle = (card.template.oracle_text or '').lower()

            # --- Urza's Saga chapters ---
            if 'construct' in card_oracle and 'artifact' in card_oracle:
                if lore == 2:
                    tokens = game.create_token(active, "construct", count=1)
                    for t in tokens:
                        if CardType.ARTIFACT not in t.template.card_types:
                            t.template.card_types.append(CardType.ARTIFACT)
                        t.template.tags.add("artifact")
                    game.log.append(f"T{game.display_turn} P{active+1}: "
                                    f"Urza's Saga Ch.II: Create Construct Token")
                elif lore >= 3:
                    tokens = game.create_token(active, "construct", count=1)
                    for t in tokens:
                        if CardType.ARTIFACT not in t.template.card_types:
                            t.template.card_types.append(CardType.ARTIFACT)
                        t.template.tags.add("artifact")
                    game.log.append(f"T{game.display_turn} P{active+1}: "
                                    f"Urza's Saga Ch.III: Create Construct Token")
                    best = None
                    best_priority = -1
                    tutor_priority = {
                        "Cranial Plating": 10, "Springleaf Drum": 5,
                        "Mox Opal": 8, "Engineered Explosives": 3,
                    }
                    # Avoid tutoring a legend we already control (dies to legend rule)
                    owned_legend_names = {
                        bc.name for bc in player.battlefield
                        if Supertype.LEGENDARY in bc.template.supertypes
                    }
                    for c in player.library:
                        if (CardType.ARTIFACT in c.template.card_types
                                and (c.template.cmc or 0) <= 1):
                            if (Supertype.LEGENDARY in c.template.supertypes
                                    and c.name in owned_legend_names):
                                continue
                            prio = tutor_priority.get(c.name, 1)
                            if prio > best_priority:
                                best = c
                                best_priority = prio
                    if best:
                        player.library.remove(best)
                        best.zone = "battlefield"
                        best.controller = active
                        player.battlefield.append(best)
                        best._game_state = game
                        game.log.append(f"T{game.display_turn} P{active+1}: "
                                        f"Urza's Saga tutors {best.name}")
                        game.trigger_etb(best, active)
                    sagas_to_sacrifice.append(card)

            # --- Fable of the Mirror-Breaker (loot + transform) ---
            # Detected by Ch.II discard/draw-that-many clause; distinct
            # from the Roku transform pattern.
            elif ('discard' in card_oracle and 'draw that many' in card_oracle):
                if lore == 2:
                    # Ch.II: discard up to 2, draw that many
                    hand_size = len(player.hand)
                    to_discard = min(2, hand_size)
                    if to_discard > 0:
                        game._force_discard(active, to_discard, self_discard=True)
                        drawn = game.draw_cards(active, to_discard)
                        names = ", ".join(c.name for c in drawn) if drawn else "0 drawn"
                        game.log.append(f"T{game.display_turn} P{active+1}: "
                                        f"{card.name} Ch.II: loot {to_discard} "
                                        f"(drew: {names})")
                elif lore >= 3:
                    # Ch.III: exile Saga, return transformed (Reflection of
                    # Kiki-Jiki — copy ability handled by activated-ability
                    # dispatch in Phase E when applicable).
                    game.log.append(f"T{game.display_turn} P{active+1}: "
                                    f"{card.name} Ch.III: transforming into "
                                    f"Reflection of Kiki-Jiki")
                    from engine.oracle_resolver import _transform_permanent
                    _transform_permanent(game, card, active)

            # --- Transform sagas (Legend of Roku pattern) ---
            elif 'transform' in card_oracle or 'return it to the battlefield transformed' in card_oracle:
                if lore == 2:
                    # Chapter II: add one mana (create Treasure as proxy)
                    game.create_token(active, "treasure", count=1)
                    game.log.append(f"T{game.display_turn} P{active+1}: "
                                    f"{card.name} Ch.II: adds mana (Treasure)")
                elif lore >= 3:
                    # Chapter III: exile saga, return transformed as creature
                    # Parse back face P/T from oracle (firebending N → N/N)
                    sagas_to_transform.append(card)

        for saga in sagas_to_sacrifice:
            if saga in player.battlefield:
                player.battlefield.remove(saga)
                if saga in player.lands:
                    player.lands.remove(saga)
                saga.zone = "graveyard"
                player.graveyard.append(saga)
                game.log.append(f"T{game.display_turn} P{active+1}: "
                                f"Sacrifice {saga.name} (final chapter)")

        for saga in sagas_to_transform:
            if saga in player.battlefield:
                player.battlefield.remove(saga)
                if saga in player.lands:
                    player.lands.remove(saga)
                # Create the transformed creature (4/4 Avatar)
                from engine.cards import Keyword
                tokens = game.create_token(
                    active, "avatar", power=4, toughness=4,
                    extra_keywords={Keyword.HASTE}
                )
                for t in tokens:
                    t.template.name = "Avatar Roku"
                    t.template.tags.add("legendary")
                saga.zone = "exile"
                player.exile.append(saga)
                game.log.append(f"T{game.display_turn} P{active+1}: "
                                f"{saga.name} Ch.III: transforms into Avatar Roku (4/4)")

    # Backward-compatible alias
    def _activate_griselbrand(self, game: GameState, active: int):
        return self._activate_pay_life_draw(game, active)

    def _process_upkeep_activations(self, game: GameState, active: int):
        """Fire generic activated abilities that auto-trigger each upkeep.

        Currently supports Isochron Scepter: if the Scepter is untapped and has
        an imprinted card, pay {2}, tap, and cast a free copy of the imprinted
        spell. Keeps the lock/value engine functional for Azorius Control.
        """
        player = game.players[active]
        for card in list(player.battlefield):
            if card.template.name != "Isochron Scepter":
                continue
            if getattr(card, 'tapped', False):
                continue
            if card.summoning_sick and "haste" not in {
                getattr(kw, 'value', str(kw).lower()) for kw in card.template.keywords
            }:
                # Artifact activations don't need summoning-sickness gating in
                # MTG, but this is a defensive guard in case the engine ever
                # treats Scepter as "sick". Skip only if truly unusable.
                continue
            imprinted_name = getattr(card, 'instance_tags', set())
            imp = None
            if isinstance(imprinted_name, set):
                imp = next((t.replace("imprint:", "") for t in imprinted_name
                            if isinstance(t, str) and t.startswith("imprint:")), None)
            if not imp:
                continue
            # Check mana: need {2} plus the imprint spell is a FREE copy.
            if player.available_mana_estimate < 2:
                continue
            # Find the imprinted spell's template somewhere — prefer exile
            # (that's where Scepter stashes it), fall back to card DB lookup.
            imp_inst = next(
                (c for c in player.exile
                 if c.template.name == imp and "on_scepter" in getattr(c, 'instance_tags', set())),
                None
            )
            if imp_inst is None:
                continue
            # Pay {2} from the pool (best-effort; full color-aware payment lives
            # in game_state.cast_spell, but Scepter's cost is strictly generic).
            paid = player.mana_pool.spend_generic(2) if hasattr(player.mana_pool, 'spend_generic') else False
            if not paid:
                # Try to auto-tap lands for 2 generic.
                tapped = 0
                for land in player.untapped_lands:
                    if tapped >= 2:
                        break
                    land.tapped = True
                    tapped += 1
                if tapped < 2:
                    continue
            card.tapped = True
            # Cast a free copy of the imprinted spell.
            try:
                game.cast_spell(active, imp_inst, free_cast=True, is_copy=True)
                game.log.append(f"T{game.display_turn} P{active+1}: "
                                f"Isochron Scepter copies {imp}")
            except TypeError:
                # cast_spell may not accept is_copy in some builds; fall back.
                try:
                    game.cast_spell(active, imp_inst, free_cast=True)
                    game.log.append(f"T{game.display_turn} P{active+1}: "
                                    f"Isochron Scepter copies {imp}")
                except Exception:
                    pass
            except Exception:
                pass

    def _activate_goblin_bombardment(self, game: GameState, active: int):
        """Activate Goblin Bombardment: sacrifice tokens/small creatures to deal 1 damage each."""
        player = game.players[active]
        opponent_idx = 1 - active
        opponent = game.players[opponent_idx]

        # Check if player has Goblin Bombardment on the battlefield
        # Generic: any permanent with "sacrifice a creature: deal 1 damage"
        has_bombardment = any(
            'sacrifice a creature' in (c.template.oracle_text or '').lower()
            and 'damage' in (c.template.oracle_text or '').lower()
            for c in player.battlefield if not c.template.is_creature
        )
        if not has_bombardment:
            return

        # Sacrifice tokens and low-value creatures to deal damage
        # Priority: tokens first, then creatures with power <= 1
        sacrificeable = []
        for c in player.creatures:
            if "token" in c.template.tags:
                sacrificeable.append((0, c))  # tokens are free to sacrifice
            elif c.template.cmc <= 1 and c.template.power <= 1:
                sacrificeable.append((1, c))  # small creatures are ok to sacrifice

        # Sort: tokens first, then by value (lowest first)
        sacrificeable.sort(key=lambda x: x[0])

        # Only sacrifice if it would deal meaningful damage or if we have excess tokens
        # Keep at least 2 creatures for blocking/attacking
        real_creatures = [c for c in player.creatures if "token" not in c.template.tags]
        token_count = len([c for c in player.creatures if "token" in c.template.tags])

        # Sacrifice all tokens if opponent is at low life (lethal range)
        if opponent.life <= token_count:
            # Go for lethal!
            for _, creature in sacrificeable:
                if game.game_over:
                    return
                if creature in player.battlefield:
                    player.battlefield.remove(creature)
                    creature.zone = "graveyard"
                    player.graveyard.append(creature)
                    opponent.life -= 1
                    player.damage_dealt_this_turn += 1
                    game.log.append(
                        f"T{game.display_turn} P{active+1}: Goblin Bombardment "
                        f"sacrifice {creature.name} -> 1 damage to P{opponent_idx+1} "
                        f"(life: {opponent.life})"
                    )
                    if opponent.life <= 0:
                        game.game_over = True
                        game.winner = active
                        return
        else:
            # Not lethal: sacrifice tokens when they provide race value.
            # Keep 1 blocker; sacrifice the rest if we can deal meaningful damage
            # or if opponent is in reach (life <= 8).
            real_blocker_count = len(real_creatures)
            min_keep = 1  # always keep at least 1 non-token creature for blocking
            tokens_to_sac = token_count  # all tokens by default
            if real_blocker_count < min_keep:
                # Keep one token as a blocker
                tokens_to_sac = max(0, token_count - 1)
            # Sacrifice tokens when they contribute to the race.
            # If opponent is out of reach AND we have few tokens, hold them for blocking.
            # But if we are racing (opponent life <= our total power * 2), sacrifice freely.
            my_total_pwr = sum(c.power or 0 for c in player.creatures)
            is_racing_now = my_total_pwr > 0 and opponent.life <= my_total_pwr * 3
            if not is_racing_now and opponent.life > 15 and tokens_to_sac < 2:
                tokens_to_sac = 0
            sacced = 0
            for priority_val, creature in sacrificeable:
                if sacced >= tokens_to_sac:
                    break

    def _activate_utility_artifacts(self, game: GameState, active: int):
        """Activate utility artifacts: Expedition Map (find Tron piece), Ratchet Bomb."""
        player = game.players[active]

        for perm in list(player.battlefield):
            oracle = (perm.template.oracle_text or '').lower()

            # Artifact with "sacrifice, search for a land" (Expedition Map, etc.)
            if ('sacrifice' in oracle and 'search' in oracle and 'land' in oracle
                    and not getattr(perm, 'tapped', False)
                    and not perm.template.is_creature):
                # Need 2 mana to activate
                untapped_count = len(player.untapped_lands)
                if untapped_count >= 2:
                    # Find missing Tron piece
                    tron_pieces = URZA_TRON_LANDS
                    on_board = {l.name for l in player.lands}
                    missing = tron_pieces - on_board
                    # Search priority: missing Tron piece > any non-basic not on board
                    target_name = None
                    if missing:
                        target_name = next(iter(missing))
                    else:
                        # Find a useful land in library not already on board
                        for c in player.library:
                            if (c.template.is_land and c.name not in on_board
                                    and len(c.template.produces_mana) >= 1):
                                target_name = c.name
                                break
                    if target_name:
                        # Pay 2 mana
                        tapped = 0
                        for land in player.untapped_lands:
                            if tapped >= 2:
                                break
                            land.tapped = True
                            tapped += 1
                        # Sacrifice Map
                        player.battlefield.remove(perm)
                        perm.zone = "graveyard"
                        player.graveyard.append(perm)
                        # Find the land in library
                        target = None
                        for c in player.library:
                            if c.name == target_name:
                                target = c
                                break
                        if target:
                            player.library.remove(target)
                            target.zone = "hand"
                            player.hand.append(target)
                            game.log.append(
                                f"T{game.display_turn} P{active+1}: "
                                f"{perm.name} finds {target_name}")

            # Charge counter artifact that ticks up and pops to destroy
            # (Ratchet Bomb, Engineered Explosives, etc.)
            if ('charge counter' in oracle and 'destroy' in oracle
                    and 'mana value' in oracle):
                charges = perm.other_counters.get("charge", 0)
                # Tick up
                perm.other_counters["charge"] = charges + 1
                new_charges = charges + 1
                # Check if we should pop it (opponent has valuable permanents at this CMC)
                opp = game.players[1 - active]
                targets_at_cmc = [c for c in opp.battlefield
                                  if not c.template.is_land
                                  and (c.template.cmc or 0) == new_charges]
                if len(targets_at_cmc) >= 2 or (
                    len(targets_at_cmc) >= 1 and any(
                        'stax' in getattr(c.template, 'tags', set()) or
                        'Equipment' in getattr(c.template, 'subtypes', [])
                        for c in targets_at_cmc)):
                    # Pop it
                    player.battlefield.remove(perm)
                    perm.zone = "graveyard"
                    player.graveyard.append(perm)
                    from engine.cards import Keyword
                    for c in list(targets_at_cmc):
                        if Keyword.INDESTRUCTIBLE not in c.keywords:
                            if c.template.is_creature:
                                game._creature_dies(c)
                            else:
                                game._permanent_destroyed(c)
                    game.log.append(
                        f"T{game.display_turn} P{active+1}: "
                        f"Ratchet Bomb ({new_charges}) destroys "
                        f"{len(targets_at_cmc)} permanents")

    def _activate_tap_abilities(self, game: GameState, active: int):
        """Generic {T}: ability dispatch for non-planeswalker permanents.

        Oracle-driven — no card names. Covers patterns like Endbringer's
        {T}: ping / {C}{C}{T}: draw and Emry's {T}: cast-artifact-from-GY.
        Skips tapped or summoning-sick creatures. One activation per
        permanent per turn (the tap state itself enforces this)."""
        import re
        from engine.cards import CardType
        from engine.oracle_resolver import _pick_damage_target
        player = game.players[active]
        opponent_idx = 1 - active

        for perm in list(player.battlefield):
            if perm.tapped:
                continue
            # Creatures need haste or to have entered before this turn
            if perm.template.is_creature and getattr(perm, 'summoning_sick', False):
                continue
            oracle = (perm.template.oracle_text or '').lower()
            if '{t}' not in oracle:
                continue

            # ── {T}: This creature deals N damage to any target. ──
            m_ping = re.search(
                r'\{t\}\s*:\s*this creature deals\s+(\d+)\s+damage to any target',
                oracle)
            if m_ping:
                amount = int(m_ping.group(1))
                target = _pick_damage_target(game, active, amount)
                perm.tapped = True
                if target is not None:
                    target.damage_marked = getattr(target, 'damage_marked', 0) + amount
                    game.log.append(
                        f"T{game.display_turn} P{active+1}: "
                        f"{perm.name} pings {target.name} for {amount}")
                    game.check_state_based_actions()
                else:
                    opp = game.players[opponent_idx]
                    opp.life -= amount
                    player.damage_dealt_this_turn += amount
                    game.log.append(
                        f"T{game.display_turn} P{active+1}: "
                        f"{perm.name} pings opponent for {amount} "
                        f"(life: {opp.life})")
                    if opp.life <= 0:
                        game.game_over = True
                        game.winner = active
                        return
                continue  # one activation per permanent per turn

            # ── {C}{C}, {T}: Draw a card. ──
            # Generic colourless-only card-draw activation. Gated on hand
            # size or losing-on-clock so we don't burn mana fixing for nothing.
            if re.search(r'\{c\}\{c\}\s*,\s*\{t\}\s*:\s*draw a card', oracle):
                if len(player.untapped_lands) < 2:
                    continue
                from ai.ev_evaluator import snapshot_from_game
                snap = snapshot_from_game(game, active)
                want_draw = (
                    len(player.hand) <= 3
                    or snap.my_clock_discrete > snap.opp_clock_discrete
                )
                if not want_draw:
                    continue
                tapped = 0
                for land in list(player.lands):
                    if tapped >= 2:
                        break
                    if not land.tapped:
                        land.tapped = True
                        tapped += 1
                if tapped < 2:
                    continue
                perm.tapped = True
                drawn = game.draw_cards(active, 1)
                drawn_name = drawn[0].name if drawn else '?'
                game.log.append(
                    f"T{game.display_turn} P{active+1}: "
                    f"{perm.name} {{C}}{{C}}, {{T}} → draw {drawn_name}")
                continue

            # ── {T}: Choose target artifact card in your graveyard.
            #        You may cast that card this turn. (Emry pattern) ──
            if 'choose target artifact card in your graveyard' in oracle:
                artifacts = [
                    c for c in player.graveyard
                    if CardType.ARTIFACT in c.template.card_types
                    and not c.template.is_land  # artifact lands aren't "cast"
                    and (c.template.cmc or 0) <= len(player.untapped_lands)
                ]
                if not artifacts:
                    continue
                # Prefer the highest-CMC affordable artifact (max value per
                # activation); ties broken by oracle length (richer effects).
                target_card = max(
                    artifacts,
                    key=lambda c: (
                        c.template.cmc or 0,
                        len(c.template.oracle_text or ''),
                    ))
                # Move GY → hand temporarily so can_cast accepts it as a
                # normal cast (Emry "you still pay its costs").
                player.graveyard.remove(target_card)
                target_card.zone = 'hand'
                player.hand.append(target_card)
                if not game.can_cast(active, target_card):
                    # Revert
                    player.hand.remove(target_card)
                    target_card.zone = 'graveyard'
                    player.graveyard.append(target_card)
                    continue
                perm.tapped = True
                game.log.append(
                    f"T{game.display_turn} P{active+1}: "
                    f"{perm.name} {{T}} → cast {target_card.name} from GY")
                game.cast_spell(active, target_card)
                continue

    def _activate_sacrifice_abilities(self, game: GameState, active: int):
        """Generic sacrifice ability activation. Parses oracle text for
        'Sacrifice this: [effect]' patterns and activates when strategically sound.
        No card names — all logic derived from oracle text."""
        import re
        player = game.players[active]
        opponent_idx = 1 - active
        opponent = game.players[opponent_idx]

        for perm in list(player.battlefield):
            oracle = (perm.template.oracle_text or '').lower()
            if 'sacrifice' not in oracle:
                continue
            # Must be "sacrifice this/~" pattern (self-sacrifice, not "sacrifice a creature")
            sac_match = re.search(
                r'sacrifice (?:this|' + re.escape(perm.template.name.split(' //')[0].lower()) + r')[^:]*:\s*(.+?)(?:\.|$)',
                oracle)
            if not sac_match:
                continue
            effect_text = sac_match.group(1).strip()

            should_activate = False

            # Draw a card: activate late game or low hand
            if 'draw a card' in effect_text:
                if len(player.hand) <= 2 or game.turn_number >= 8:
                    should_activate = True

            # Destroy by MV (EE, Blast Zone)
            elif 'destroy' in effect_text and 'mana value' in effect_text:
                charge = perm.other_counters.get("charge", 0)
                # Don't pop at charge=0 (kills own tokens/0-cost artifacts)
                if charge >= 1:
                    hits = sum(1 for c in opponent.battlefield
                               if not c.template.is_land and (c.template.cmc or 0) == charge)
                    if hits >= 1:
                        should_activate = True

            # Exile graveyard
            elif 'exile' in effect_text and 'graveyard' in effect_text:
                if len(opponent.graveyard) >= 5:
                    should_activate = True

            # Search for land
            elif 'search' in effect_text and 'land' in effect_text:
                expensive = any((c.template.cmc or 0) > len(player.lands)
                                for c in player.hand if not c.template.is_land)
                if expensive and game.turn_number >= 3:
                    should_activate = True

            # Exile artifact/enchantment
            elif 'exile' in effect_text and ('artifact' in effect_text or 'enchantment' in effect_text):
                targets = [c for c in opponent.battlefield
                           if not c.template.is_creature and not c.template.is_land]
                if targets:
                    should_activate = True

            # Deal damage
            elif re.search(r'(\d+) damage', effect_text):
                dmg = int(re.search(r'(\d+) damage', effect_text).group(1))
                if dmg >= opponent.life:
                    should_activate = True

            # Return lands from GY
            elif 'return' in effect_text and 'land' in effect_text:
                gy_lands = sum(1 for c in player.graveyard if c.template.is_land)
                if gy_lands >= 2:
                    should_activate = True

            if should_activate and perm in player.battlefield:
                player.battlefield.remove(perm)
                perm.zone = "graveyard"
                player.graveyard.append(perm)
                game.log.append(f"T{game.display_turn} P{active+1}: "
                                f"Activate {perm.name} (sacrifice)")
                self._resolve_sac_effect(game, active, perm, effect_text)
                if game.game_over:
                    return

    def _resolve_sac_effect(self, game: GameState, controller: int, sacrificed, effect_text: str):
        """Execute sacrifice ability effect, parsed from oracle text."""
        import re
        player = game.players[controller]
        opp_idx = 1 - controller
        opp = game.players[opp_idx]

        if 'draw a card' in effect_text:
            game.draw_cards(controller, 1)
        elif 'destroy' in effect_text and 'mana value' in effect_text:
            charge = sacrificed.other_counters.get("charge", 0)
            # Only destroy OPPONENT's permanents (Blast Zone/EE target opponents)
            from engine.cards import Keyword
            for c in list(opp.battlefield):
                if not c.template.is_land and (c.template.cmc or 0) == charge:
                    if Keyword.INDESTRUCTIBLE not in c.keywords:
                        if c.template.is_creature:
                            game._creature_dies(c)
                        else:
                            game._permanent_destroyed(c)
                        game.log.append(f"T{game.display_turn} P{controller+1}: "
                                        f"  destroys {c.name} (MV={charge})")
        elif 'exile' in effect_text and 'graveyard' in effect_text:
            for c in list(opp.graveyard):
                opp.graveyard.remove(c)
                c.zone = "exile"
                opp.exile.append(c)
        elif 'search' in effect_text and 'land' in effect_text:
            # Smart land search: find missing Tron piece first, then any land
            tron_pieces = URZA_TRON_LANDS
            on_board = {l.name for l in player.lands}
            in_hand = {c.name for c in player.hand if c.template.is_land}
            missing_tron = tron_pieces - on_board - in_hand
            target = None
            for c in player.library:
                if c.template.is_land and c.name in missing_tron:
                    target = c
                    break
            if not target:
                # Fallback: find Eldrazi Temple or any useful land
                for c in player.library:
                    if c.template.is_land and c.name not in on_board:
                        target = c
                        break
            if not target:
                lands = [c for c in player.library if c.template.is_land]
                if lands:
                    target = lands[0]
            if target:
                player.library.remove(target)
                target.zone = "hand"
                player.hand.append(target)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                                f"  finds {target.name}")
                game.rng.shuffle(player.library)
        elif 'exile' in effect_text and ('artifact' in effect_text or 'enchantment' in effect_text):
            targets = [c for c in opp.battlefield
                       if not c.template.is_creature and not c.template.is_land]
            if targets:
                t = max(targets, key=lambda c: c.template.cmc or 0)
                opp.battlefield.remove(t)
                t.zone = "exile"
                opp.exile.append(t)
        elif re.search(r'(\d+) damage', effect_text):
            dmg = int(re.search(r'(\d+) damage', effect_text).group(1))
            opp.life -= dmg
            if opp.life <= 0:
                game.game_over = True
                game.winner = controller
        elif 'return' in effect_text and 'land' in effect_text:
            for c in list(player.graveyard):
                if c.template.is_land:
                    player.graveyard.remove(c)
                    c.zone = "battlefield"
                    c.tapped = True
                    player.battlefield.append(c)
                    opp.life -= 1
                    player.damage_dealt_this_turn += 1
                    game.log.append(
                        f"T{game.display_turn} P{controller+1}: "
                        f"Returned {c.name} to battlefield — 1 damage to P{opp_idx+1} "
                        f"(life: {opp.life})"
                    )
                    if opp.life <= 0:
                        game.game_over = True
                        game.winner = controller
                        return
