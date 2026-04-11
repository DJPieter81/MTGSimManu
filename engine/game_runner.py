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

from ai.ev_player import EVPlayer as AIPlayer
from ai.board_eval import evaluate_action, Action, ActionType
from ai.mana_planner import analyze_mana_needs, choose_fetch_target


class AICallbacks(GameCallbacks):
    """Wires engine callbacks to AI decision functions."""

    def should_shock_land(self, game, player_idx, land):
        """EV-based decision: pay life for untapped land?

        Projects two snapshots — one where we pay (untapped, lose life)
        and one where we don't (tapped, keep life) — and compares them
        using evaluate_board(). Works for any land with untap_life_cost.
        """
        from ai.ev_evaluator import snapshot_from_game, evaluate_board
        from ai.strategy_profile import DECK_ARCHETYPES

        player = game.players[player_idx]
        life_cost = land.template.untap_life_cost if hasattr(land, 'template') else 2

        # Hard floor: never pay life to death
        if player.life <= life_cost:
            return False

        # Determine archetype for evaluation
        deck_name = player.deck_name
        arch_enum = DECK_ARCHETYPES.get(deck_name)
        archetype = arch_enum.value if arch_enum else "midrange"

        # Snapshot the current state
        snap = snapshot_from_game(game, player_idx)

        # Project "paid" state: -life_cost life, +1 untapped mana
        shocked = snap.__class__(
            **{f.name: getattr(snap, f.name) for f in snap.__dataclass_fields__.values()}
        )
        shocked.my_life = snap.my_life - life_cost
        shocked.my_mana = snap.my_mana + 1
        shocked.my_total_lands = snap.my_total_lands + 1

        # Project "tapped" state: keep life, land is tapped (no mana this turn)
        tapped = snap.__class__(
            **{f.name: getattr(snap, f.name) for f in snap.__dataclass_fields__.values()}
        )
        tapped.my_total_lands = snap.my_total_lands + 1
        # Tapped land: mana available doesn't increase this turn

        # Compare board values
        shocked_value = evaluate_board(shocked, archetype)
        tapped_value = evaluate_board(tapped, archetype)

        # Also factor in spell enablement: if shocking enables a spell
        # we couldn't cast otherwise, that's a significant bonus
        existing_colors = set()
        for l in player.lands:
            existing_colors.update(l.template.produces_mana)
        land_colors = set(land.template.produces_mana)
        combined = existing_colors | land_colors

        enables_spell = False
        mana_if_shocked = len(player.untapped_lands) + 1
        mana_if_tapped = len(player.untapped_lands)
        for card in player.hand:
            if card.template.is_land:
                continue
            cmc = card.template.cmc or 0
            if cmc == 0 or cmc > mana_if_shocked:
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

        # Shock if: projected board value is better shocked,
        # OR shocking enables a spell we couldn't cast otherwise
        return shocked_value > tapped_value or enables_spell

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
            result = self.run_game(
                deck1_name, d1_main,
                deck2_name, d2_main,
                deck1_sideboard=d1_side,
                deck2_sideboard=d2_side,
                verbose=verbose,
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
                  deck2_sideboard: Dict[str, int] = None) -> GameResult:
        """Run a complete game and return the result."""
        deck1 = self.build_deck(deck1_list)
        deck2 = self.build_deck(deck2_list)

        game = GameState(rng=self.rng, callbacks=AICallbacks())
        game.setup_game(deck1, deck2)
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
        game.log.append(f"Die roll: {game.players[on_play].deck_name} wins the die roll (goes first)")

        # Mulligan phase
        mulligan_counts = [0, 0]
        for p_idx in range(2):
            hand_size = 7
            opening = [c.name for c in game.players[p_idx].hand]
            game.log.append(f"P{p_idx+1} ({game.players[p_idx].deck_name}) opening hand: {opening}")
            while hand_size >= 5:
                player = game.players[p_idx]
                keep = ais[p_idx].decide_mulligan(player.hand, hand_size)
                if keep:
                    if mulligan_counts[p_idx] > 0:
                        to_bottom = ais[p_idx].choose_cards_to_bottom(
                            player.hand, mulligan_counts[p_idx])
                        bottom_names = [c.name for c in to_bottom]
                        for card in to_bottom:
                            player.hand.remove(card)
                            card.zone = "library"
                            player.library.append(card)
                        kept = [c.name for c in player.hand]
                        game.log.append(
                            f"P{p_idx+1} mulligans to {hand_size}, "
                            f"bottoms: {bottom_names}, keeps: {kept}")
                    else:
                        game.log.append(f"P{p_idx+1} keeps 7")
                    break
                else:
                    mulligan_counts[p_idx] += 1
                    game.log.append(f"P{p_idx+1} mulligans (hand {hand_size} -> {hand_size-1})")
                    for card in player.hand[:]:
                        player.hand.remove(card)
                        card.zone = "library"
                        player.library.append(card)
                    self.rng.shuffle(player.library)
                    game.draw_cards(p_idx, 7)
                    hand_size -= 1

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

            for step in turn_mgr.iterate_turn(game):
                if game.game_over:
                    break
                if _time.monotonic() > game._game_deadline:
                    game.game_over = True
                    break

                if step == TurnStep.UNTAP:
                    game.current_phase = Phase.UNTAP
                    game.untap_step(active)
                    # Reset planeswalker activation tracking for this turn
                    ai._pw_activated_this_turn.clear()

                elif step == TurnStep.UPKEEP:
                    game.current_phase = Phase.UPKEEP
                    # Rebound: cast exiled rebound spells for free
                    if hasattr(game, '_rebound_cards'):
                        to_cast = [c for c in game._rebound_cards
                                   if getattr(c, '_rebound_controller', -1) == active]
                        for rc in to_cast:
                            game._rebound_cards.remove(rc)
                            if rc in game.players[active].exile:
                                game.players[active].exile.remove(rc)
                            game.cast_spell(active, rc, free_cast=True)
                            game.log.append(f"T{game.turn_number} P{active+1}: "
                                            f"Rebound {rc.name}")
                    # Urza's Saga chapter triggers
                    self._process_saga_chapters(game, active)
                    game.process_triggers()
                    self._resolve_stack_loop(game)

                elif step == TurnStep.DRAW:
                    game.current_phase = Phase.DRAW
                    if not turn_mgr.should_skip_draw(game):
                        game.draw_cards(active, 1)

                elif step == TurnStep.MAIN1:
                    game.current_phase = Phase.MAIN1
                    prev_lands = len(game.players[active].lands)
                    self._execute_main_phase(game, ai, opponent_ai)
                    if game.game_over:
                        break
                    new_lands = len(game.players[active].lands) - prev_lands
                    stats["lands_played"][active] += max(0, new_lands)
                    self._activate_planeswalkers(game, ai)
                    if game.game_over:
                        break
                    self._activate_griselbrand(game, active)

                elif step == TurnStep.BEGIN_COMBAT:
                    game.current_phase = Phase.BEGIN_COMBAT
                    # Per CR 500.4: empty mana pools between phases
                    for p in game.players:
                        p.mana_pool.empty()
                    # Priority window: opponent can cast instants before combat
                    self._opponent_instant_window(game, opponent_ai, ai)

                elif step == TurnStep.DECLARE_ATTACKERS:
                    game.current_phase = Phase.DECLARE_ATTACKERS
                    attackers = ai.decide_attackers(game)
                    if attackers:
                        combat_mgr.declare_attackers(game, attackers, active)

                elif step == TurnStep.AFTER_ATTACKERS_DECLARED:
                    if combat_mgr.attackers:
                        # Priority window after attackers declared
                        game.process_triggers()
                        self._resolve_stack_loop(game)

                elif step == TurnStep.DECLARE_BLOCKERS:
                    if combat_mgr.attackers:
                        game.current_phase = Phase.DECLARE_BLOCKERS
                        blocks = opponent_ai.decide_blockers(game, combat_mgr.attackers)
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
                        if game.game_over:
                            break

                elif step == TurnStep.END_COMBAT:
                    game.current_phase = Phase.END_COMBAT
                    combat_mgr.end_combat(game)

                elif step == TurnStep.MAIN2:
                    game.current_phase = Phase.MAIN2
                    # Per CR 500.4: empty mana pools between phases
                    for p in game.players:
                        p.mana_pool.empty()
                    self._execute_main_phase(game, ai, opponent_ai)
                    if game.game_over:
                        break
                    self._activate_planeswalkers(game, ai)
                    if game.game_over:
                        break
                    # Griselbrand activation in MAIN2 (in case reanimated during MAIN2)
                    self._activate_griselbrand(game, active)
                    if game.game_over:
                        break
                    stats["spells_cast"][active] += game.players[active].spells_cast_this_turn

                elif step == TurnStep.END_STEP:
                    game.current_phase = Phase.END_STEP
                    # Goblin Bombardment: sacrifice tokens/small creatures to deal damage
                    self._activate_goblin_bombardment(game, active)
                    if game.game_over:
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
            turns=game.turn_number,
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
        """
        from ai.evaluator import _permanent_value

        opponent_idx = opponent_ai.player_idx
        opponent = game.players[opponent_idx]  # the player holding removal
        active_player = game.players[active_ai.player_idx]  # the player whose turn it is

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
                    # Opponent gets priority to respond (CR 117.3d)
                    if not game.stack.is_empty:
                        top = game.stack.top
                        if top:
                            priority.give_priority(game, opponent_ai.player_idx)
                            response = opponent_ai.decide_response(game, top)
                            if response:
                                resp_card, resp_targets = response
                                game.cast_spell(opponent_ai.player_idx,
                                                resp_card, resp_targets)
                                priority.take_action(game)
                                # BHI: opponent cast a response — update beliefs
                                if hasattr(ai, 'bhi'):
                                    ai.bhi.observe_spell_cast(
                                        game, getattr(resp_card.template, 'tags', set()))
                            else:
                                # BHI: opponent passed — update beliefs
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
            pw_data = _parse_planeswalker_abilities(
                pw.template.oracle_text, pw.template.loyalty)
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
                game.log.append(f"T{game.turn_number} P{active+1}: "
                                f"{creature.name}: pay {life_cost} life, draw {draw_count}")
                activations += 1
                if game.game_over:
                    return
            break  # only activate one such creature per call

    def _process_saga_chapters(self, game: GameState, active: int):
        """Process Urza's Saga chapter triggers during upkeep.

        Urza's Saga gains a lore counter each turn (starting the turn after it
        enters). Chapter I: {C} mana (handled by land). Chapter II: create a
        Construct token with P/T = artifact count. Chapter III: search library
        for 0-1 CMC artifact, put on battlefield, then sacrifice the Saga.
        """
        from engine.cards import CardType
        player = game.players[active]
        sagas_to_sacrifice = []
        for card in list(player.battlefield):
            if card.template.name != "Urza's Saga":
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

            if lore == 2:
                # Chapter II: create Construct token
                tokens = game.create_token(active, "construct", count=1)
                # Mark as artifact for artifact count
                for t in tokens:
                    if CardType.ARTIFACT not in t.template.card_types:
                        t.template.card_types.append(CardType.ARTIFACT)
                    t.template.tags.add("artifact")
                game.log.append(f"T{game.turn_number} P{active+1}: "
                                f"Urza's Saga Ch.II: Create Construct Token")
            elif lore >= 3:
                # Chapter III: create Construct + tutor 0-1 CMC artifact
                tokens = game.create_token(active, "construct", count=1)
                for t in tokens:
                    if CardType.ARTIFACT not in t.template.card_types:
                        t.template.card_types.append(CardType.ARTIFACT)
                    t.template.tags.add("artifact")
                game.log.append(f"T{game.turn_number} P{active+1}: "
                                f"Urza's Saga Ch.III: Create Construct Token")
                # Tutor for 0 or 1 CMC artifact from library
                best = None
                best_priority = -1
                # Priority: Cranial Plating > Springleaf Drum > Mox Opal > others
                tutor_priority = {
                    "Cranial Plating": 10, "Springleaf Drum": 5,
                    "Mox Opal": 8, "Engineered Explosives": 3,
                }
                for c in player.library:
                    if (CardType.ARTIFACT in c.template.card_types
                            and (c.template.cmc or 0) <= 1):
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
                    game.log.append(f"T{game.turn_number} P{active+1}: "
                                    f"Urza's Saga tutors {best.name}")
                    # Fire ETB if registered
                    game.trigger_etb(best, active)
                # Sacrifice the saga
                sagas_to_sacrifice.append(card)

        for saga in sagas_to_sacrifice:
            if saga in player.battlefield:
                player.battlefield.remove(saga)
                if saga in player.lands:
                    player.lands.remove(saga)
                saga.zone = "graveyard"
                player.graveyard.append(saga)
                game.log.append(f"T{game.turn_number} P{active+1}: "
                                f"Sacrifice Urza's Saga (final chapter)")

    # Backward-compatible alias
    def _activate_griselbrand(self, game: GameState, active: int):
        return self._activate_pay_life_draw(game, active)

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
                        f"T{game.turn_number} P{active+1}: Goblin Bombardment "
                        f"sacrifice {creature.name} -> 1 damage to P{opponent_idx+1} "
                        f"(life: {opponent.life})"
                    )
                    if opponent.life <= 0:
                        game.game_over = True
                        game.winner = active
                        return
        else:
            # Not lethal: only sacrifice excess tokens (keep 2 for blocking)
            tokens_to_sac = max(0, token_count - 2)
            sacced = 0
            for priority_val, creature in sacrificeable:
                if sacced >= tokens_to_sac:
                    break
                if priority_val > 0:
                    break  # Don't sacrifice real creatures if not going for lethal
                if creature in player.battlefield:
                    player.battlefield.remove(creature)
                    creature.zone = "graveyard"
                    player.graveyard.append(creature)
                    opponent.life -= 1
                    player.damage_dealt_this_turn += 1
                    sacced += 1
                    game.log.append(
                        f"T{game.turn_number} P{active+1}: Goblin Bombardment "
                        f"sacrifice {creature.name} -> 1 damage to P{opponent_idx+1} "
                        f"(life: {opponent.life})"
                    )
                    if opponent.life <= 0:
                        game.game_over = True
                        game.winner = active
                        return
