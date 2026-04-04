"""Dump a full Zoo vs 4c Omnath game — every log line + every AI decision interleaved chronologically."""
import random, sys
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS

db = CardDatabase()
runner = GameRunner(db)
seed = 100000

output_lines = []

# Patch game_state log to capture lines as they happen
import engine.game_state as gs
_orig_init = gs.GameState.__init__
def _patched_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    self._real_log = self.log
    class LiveLog(list):
        def append(inner_self, item):
            super().append(item)
            output_lines.append(f"[GAME] {item}")
    self.log = LiveLog(self._real_log)
gs.GameState.__init__ = _patched_init

# Patch choose_spell to log reasoning
import ai.spell_decision as sd
_orig_choose = sd.choose_spell
def _traced_choose(engine, castable, game, player_idx, assessment):
    decision = _orig_choose(engine, castable, game, player_idx, assessment)
    p = player_idx + 1
    turn = game.turn_number
    hand_spells = [c.name for c in game.players[player_idx].hand if not c.template.is_land]
    hand_lands = [c.name for c in game.players[player_idx].hand if c.template.is_land]
    castable_names = [c.name for c in castable]
    board = [c.name for c in game.players[player_idx].creatures]
    opp_board = [c.name for c in game.players[1 - player_idx].creatures]
    lands = [c.name for c in game.players[player_idx].lands]
    untapped_lands = [c.name for c in game.players[player_idx].lands if not c.tapped]
    mana = assessment.my_mana
    life = assessment.my_life
    opp_life = assessment.opp_life
    colors = getattr(assessment, 'colors_available', set())

    lines = []
    lines.append(f"[STRATEGY T{turn} P{p}]")
    lines.append(f"  Life: {life} | Opp Life: {opp_life}")
    lines.append(f"  Lands ({len(lands)}): {lands}")
    lines.append(f"  Untapped ({len(untapped_lands)}, mana={mana}): {untapped_lands}")
    lines.append(f"  Colors: {colors}")
    lines.append(f"  Hand (spells): {hand_spells}")
    lines.append(f"  Hand (lands): {hand_lands}")
    lines.append(f"  Castable: {castable_names}")
    lines.append(f"  My board: {board}")
    lines.append(f"  Opp board: {opp_board}")
    lines.append(f"  Current goal: {engine.current_goal.description}")
    if decision.card:
        lines.append(f"  DECISION: {decision.concern.upper()} -> Cast {decision.card.name}")
        lines.append(f"  Reasoning: {decision.reasoning}")
        if decision.alternatives:
            alts = [f"{n} ({r})" for n, r in decision.alternatives[:3]]
            lines.append(f"  Alternatives: {', '.join(alts)}")
    else:
        lines.append(f"  DECISION: PASS")
        lines.append(f"  Reasoning: {decision.reasoning}")
    lines.append("")
    for l in lines:
        output_lines.append(l)
    return decision
sd.choose_spell = _traced_choose

# Patch decide_response to log counterspell/response decisions
import ai.ai_player as aip
_orig_decide_response = aip.AIPlayer.decide_response
def _traced_response(self, game, stack_item):
    result = _orig_decide_response(self, game, stack_item)
    p = self.player_idx + 1
    turn = game.turn_number
    spell_name = stack_item.name if hasattr(stack_item, 'name') else str(stack_item)
    if result:
        resp_name = result[0].name if hasattr(result[0], 'name') else str(result[0])
        output_lines.append(f"[RESPONSE T{turn} P{p}] Responding to {spell_name} with {resp_name}")
    return result
aip.AIPlayer.decide_response = _traced_response

# Run
runner.rng = random.Random(seed)
zoo = MODERN_DECKS['Domain Zoo']
omnath = MODERN_DECKS['4c Omnath']
result = runner.run_game('Domain Zoo', zoo['mainboard'], '4c Omnath', omnath['mainboard'], verbose=True)

# Print everything
print(f"SEED {seed}: Domain Zoo (P1) vs 4c Omnath (P2)")
print(f"Winner: P{result.winner+1} ({'Domain Zoo' if result.winner==0 else '4c Omnath'}) in {result.turns} turns")
print(f"Winner life: {result.winner_life}, Loser life: {result.loser_life}")
print("=" * 80)
for line in output_lines:
    print(line)
