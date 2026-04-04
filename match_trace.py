"""Detailed match trace — turn-by-turn play-by-play with full AI reasoning."""
import random
import sys
sys.path.insert(0, '/home/user/MTGSimManu')

from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game, creature_value
from engine.game_state import GameState, Phase

db = CardDatabase()
runner = GameRunner(db)

# ── Patch everything to capture events ──

events = []  # list of (turn, phase, player, event_type, detail)

orig_main = EVPlayer.decide_main_phase
orig_atk = EVPlayer.decide_attackers
orig_blk = EVPlayer.decide_blockers
orig_resp = EVPlayer.decide_response
orig_play_land = GameState.play_land
orig_cast_spell = GameState.cast_spell
orig_combat_damage = None  # patched below
orig_draw = GameState.draw_cards
orig_check_sba = GameState.check_state_based_actions

turn_phase_headers = set()

def deck_short(name):
    if 'Zoo' in name: return 'Zoo'
    if 'Omnath' in name: return 'Omnath'
    if 'Dimir' in name: return 'Dimir'
    return name[:8]

def log(msg):
    events.append(msg)

def patched_main(self, game, excluded=None):
    me = game.players[self.player_idx]
    opp = game.players[1 - self.player_idx]
    snap = snapshot_from_game(game, self.player_idx)
    ds = deck_short(me.deck_name)
    phase = str(game.current_phase).split('.')[-1]

    # Header for this decision point
    hdr_key = (game.turn_number, self.player_idx, phase)
    if hdr_key not in turn_phase_headers:
        turn_phase_headers.add(hdr_key)
        my_board = [f"{c.name} ({c.power}/{c.toughness})" for c in me.creatures]
        opp_board = [f"{c.name} ({c.power}/{c.toughness})" for c in opp.creatures]
        my_other = [c.name for c in me.battlefield if not c.template.is_creature and not c.template.is_land]
        opp_other = [c.name for c in opp.battlefield if not c.template.is_creature and not c.template.is_land]
        hand_spells = [c.name for c in me.hand if not c.template.is_land]
        hand_lands = [c.name for c in me.hand if c.template.is_land]
        my_lands = [c.name for c in me.lands]

        log("")
        log(f"  ╔═ TURN {game.turn_number} — {phase} — {me.deck_name}'s turn ═╗")
        log(f"  ║ Life: {ds} {me.life} vs {deck_short(opp.deck_name)} {opp.life}")
        log(f"  ║ {ds} board: {', '.join(my_board) if my_board else '(empty)'}")
        if my_other:
            log(f"  ║ {ds} other permanents: {', '.join(my_other)}")
        log(f"  ║ {ds} lands ({len(my_lands)}): {', '.join(my_lands[:6])}")
        log(f"  ║ {deck_short(opp.deck_name)} board: {', '.join(opp_board) if opp_board else '(empty)'}")
        if opp_other:
            log(f"  ║ {deck_short(opp.deck_name)} other permanents: {', '.join(opp_other)}")
        log(f"  ║ Hand ({len(me.hand)}): {', '.join(hand_spells) if hand_spells else '(no spells)'}")
        if hand_lands:
            log(f"  ║ Hand lands: {', '.join(hand_lands)}")
        log(f"  ║ Available mana: {snap.my_mana}")
        log(f"  ╚{'═' * 50}╝")

    result = orig_main(self, game, excluded)

    # Score ALL candidates for reasoning display
    all_candidates = []
    for sp in me.hand:
        if sp.template.is_land:
            continue
        if not game.can_cast(self.player_idx, sp):
            continue
        tags = getattr(sp.template, 'tags', set())
        if 'counterspell' in tags and 'removal' not in tags:
            continue
        try:
            sp_ev = self._score_spell(sp, snap, game, me, opp)
            all_candidates.append((sp.name, sp_ev))
        except Exception:
            pass

    if result:
        action, card, targets = result
        if action == "play_land":
            tapped = "tapped" if card.template.enters_tapped else "untapped"
            colors = ', '.join(card.template.produces_mana) if card.template.produces_mana else '?'
            log(f"  ▶ {ds} plays land: {card.name} ({tapped}, produces {colors})")
        elif action == "cast_spell":
            ev = self._score_spell(card, snap, game, me, opp)
            target_desc = ""
            if targets:
                for t in targets:
                    if t == -1:
                        target_desc = " targeting opponent's face"
                    else:
                        tc = game.get_card_by_id(t) if hasattr(game, 'get_card_by_id') else None
                        if tc:
                            target_desc = f" targeting {tc.name}"
            cmc = card.template.cmc or 0
            log(f"  ▶ {ds} casts {card.name} (CMC {cmc}){target_desc}")
            log(f"    EV={ev:.1f} — BEST available play")
            # Show alternatives
            alternatives = sorted(all_candidates, key=lambda x: -x[1])
            alt_strs = [f"{n} ({v:.1f})" for n, v in alternatives if n != card.name][:3]
            if alt_strs:
                log(f"    Alternatives considered: {', '.join(alt_strs)}")
    else:
        if all_candidates:
            best = max(all_candidates, key=lambda x: x[1])
            log(f"  ▶ {ds} passes — best option {best[0]} (EV={best[1]:.1f}) below threshold")
        else:
            log(f"  ▶ {ds} passes — nothing castable")

    return result

def patched_atk(self, game):
    result = orig_atk(self, game)
    me = game.players[self.player_idx]
    opp = game.players[1 - self.player_idx]
    ds = deck_short(me.deck_name)

    if result:
        total_power = sum(c.power or 0 for c in result)
        names = [f"{c.name} ({c.power}/{c.toughness})" for c in result]
        held = [c for c in game.get_valid_attackers(self.player_idx) if c not in result]
        log(f"  ⚔ {ds} attacks with: {', '.join(names)}")
        log(f"    Total power: {total_power} vs {opp.life} life")
        if held:
            log(f"    Holding back: {', '.join(c.name for c in held)}")
        if total_power >= opp.life:
            log(f"    ★ LETHAL ON BOARD! ★")
    else:
        valid = game.get_valid_attackers(self.player_idx)
        if valid:
            log(f"  ⚔ {ds} holds all creatures back (not profitable to attack)")
    return result

def patched_blk(self, game, attackers):
    result = orig_blk(self, game, attackers)
    me = game.players[self.player_idx]
    ds = deck_short(me.deck_name)

    if result:
        for atk_id, blk_ids in result.items():
            atk = next((a for a in attackers if a.instance_id == atk_id), None)
            blks = [game.get_card_by_id(bid) for bid in blk_ids]
            blk_names = [f"{b.name} ({b.power}/{b.toughness})" for b in blks if b]
            if atk and blk_names:
                log(f"  🛡 {ds} blocks {atk.name} ({atk.power}/{atk.toughness}) with {', '.join(blk_names)}")
    else:
        if attackers and me.creatures:
            incoming = sum(a.power or 0 for a in attackers)
            log(f"  🛡 {ds} takes {incoming} damage unblocked")
        elif attackers:
            incoming = sum(a.power or 0 for a in attackers)
            log(f"  🛡 {ds} has no blockers — takes {incoming} damage")

    return result

def patched_resp(self, game, stack_item):
    result = orig_resp(self, game, stack_item)
    me = game.players[self.player_idx]
    ds = deck_short(me.deck_name)
    if result and stack_item:
        card, targets = result
        spell_name = stack_item.card.name if hasattr(stack_item, 'card') and stack_item.card else '?'
        log(f"  ⚡ {ds} responds to {spell_name} with {card.name}!")
    return result

EVPlayer.decide_main_phase = patched_main
EVPlayer.decide_attackers = patched_atk
EVPlayer.decide_blockers = patched_blk
EVPlayer.decide_response = patched_resp

# ── Run the match ──

random.seed(20000)
d1_data = MODERN_DECKS["Domain Zoo"]
d2_data = MODERN_DECKS["4c Omnath"]

d1_main = dict(d1_data["mainboard"])
d1_side = dict(d1_data.get("sideboard", {}))
d2_main = dict(d2_data["mainboard"])
d2_side = dict(d2_data.get("sideboard", {}))

match_score = [0, 0]
deck_names = ["Domain Zoo", "4c Omnath"]

for game_num in range(1, 4):
    events.clear()
    turn_phase_headers.clear()

    log("")
    log("=" * 60)
    log(f"  GAME {game_num} OF 3: {deck_names[0]} vs {deck_names[1]}")
    log(f"  Match score: Zoo {match_score[0]} — Omnath {match_score[1]}")
    log("=" * 60)

    result = runner.run_game(
        deck_names[0], d1_main,
        deck_names[1], d2_main,
        deck1_sideboard=d1_side,
        deck2_sideboard=d2_side,
        verbose=True,
    )

    # Print engine log events we missed (combat damage, life changes, etc.)
    # We already captured everything via patches, but add the result
    log("")
    log("─" * 60)
    w = result.winner
    if w is not None:
        winner_name = deck_names[w]
        loser_name = deck_names[1 - w]
        log(f"  ★ {winner_name} WINS Game {game_num} in {result.turns} turns! ({result.win_condition})")
        log(f"  Final life: {deck_names[0]} {result.winner_life if w==0 else result.loser_life}"
            f" / {deck_names[1]} {result.winner_life if w==1 else result.loser_life}")
        match_score[w] += 1
    else:
        log(f"  Game {game_num} is a DRAW ({result.win_condition})")
    log(f"  {deck_names[0]}: {result.deck1_spells_cast} spells cast, {result.deck1_lands_played} lands played")
    log(f"  {deck_names[1]}: {result.deck2_spells_cast} spells cast, {result.deck2_lands_played} lands played")
    log("─" * 60)

    # Print all events for this game
    for e in events:
        print(e)

    # Check if match is decided
    if match_score[0] >= 2 or match_score[1] >= 2:
        break

print()
print("=" * 60)
if match_score[0] > match_score[1]:
    print(f"  ★★★ {deck_names[0]} WINS THE MATCH {match_score[0]}-{match_score[1]} ★★★")
elif match_score[1] > match_score[0]:
    print(f"  ★★★ {deck_names[1]} WINS THE MATCH {match_score[1]}-{match_score[0]} ★★★")
else:
    print(f"  Match tied {match_score[0]}-{match_score[1]}")
print("=" * 60)

# Restore
EVPlayer.decide_main_phase = orig_main
EVPlayer.decide_attackers = orig_atk
EVPlayer.decide_blockers = orig_blk
EVPlayer.decide_response = orig_resp
