"""Run Bo3 matches with full AI decision reasoning inlined into the game log.

Composes the existing `run_bo3` flow with the `traced_main`/`traced_atk`
patches from `run_trace_game` — no new sim logic, just re-wiring where
the trace output lands so it interleaves with the verbose game log.

Usage:
    python tools/bo3_trace.py boros affinity 63500
    python tools/bo3_trace.py boros affinity 63500 63500_boros_vs_aff.txt
"""
from __future__ import annotations

import sys
import os

# Allow running as a script from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.ev_player import EVPlayer
from run_meta import run_bo3, resolve_deck_name


def _make_traced_main(orig):
    def traced_main(self, game, excluded_cards=None):
        # Run the real decision first — no re-scoring, no RNG divergence
        result = orig(self, game, excluded_cards)

        log = game.log  # inline into verbose game log
        phase_obj = getattr(game, 'current_phase', None)
        phase_label = getattr(phase_obj, 'name', str(phase_obj) if phase_obj else 'Main?')
        log.append(f'  [AI-TRACE {phase_label}] T{game.turn_number} {self.deck_name}')

        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        hand_spells = [c.name for c in me.hand if not c.template.is_land]
        hand_lands = sum(1 for c in me.hand if c.template.is_land)
        mana = me.available_mana_estimate + me.mana_pool.total()
        bf_creatures = [f'{c.name} ({c.power}/{c.toughness})' for c in me.creatures]
        bf_other = [c.name for c in me.battlefield
                    if not c.template.is_creature and not c.template.is_land]
        opp_creatures = [f'{c.name} ({c.power}/{c.toughness})' for c in opp.creatures]
        gy_count = len(me.graveyard)

        log.append(f'    state: life={me.life} mana={mana} '
                   f'hand={len(hand_spells)}+{hand_lands}L gy={gy_count}')
        log.append(f'    hand: {hand_spells}')
        if bf_creatures:
            log.append(f'    board: {bf_creatures}')
        if bf_other:
            log.append(f'    perms: {bf_other}')
        log.append(f'    opp: {opp_creatures} (life={opp.life})')

        candidates = getattr(self, '_last_candidates', [])
        valid_ids = {c.instance_id for c in me.hand} | {c.instance_id for c in me.battlefield}
        candidates = [p for p in candidates if p.card.instance_id in valid_ids]
        if candidates:
            log.append(f'    EV candidates:')
            for play in candidates[:6]:
                marker = ' <--' if play is candidates[0] else ''
                base = f'      {play.ev:+6.1f}  {play.action}: {play.card.name}{marker}'
                if play.action == "cast_spell" and play.lookahead_ev != 0:
                    h = play.heuristic_ev
                    la = play.lookahead_ev
                    parts = [f'h={h:+.1f} la={la:+.1f}']
                    if play.counter_pct > 0:
                        parts.append(f'ctr={play.counter_pct:.0%}')
                    if play.removal_pct > 0:
                        parts.append(f'rmv={play.removal_pct:.0%}')
                    base += f'  [{" ".join(parts)}]'
                log.append(base)
            if len(candidates) > 6:
                log.append(f'      ... +{len(candidates)-6} more')

        if result:
            log.append(f'    >>> {result[0].upper()}: {result[1].name}')
        else:
            log.append(f'    >>> PASS (threshold={self.profile.pass_threshold})')
        return result
    return traced_main


def _make_traced_atk(orig):
    def traced_atk(self, game):
        result = orig(self, game)
        log = game.log
        if result:
            names = [c.name for c in result]
            log.append(f'  [AI-TRACE ATTACK] T{game.turn_number} {self.deck_name}: {names}')
        else:
            log.append(f'  [AI-TRACE ATTACK] T{game.turn_number} {self.deck_name}: no attack')
        return result
    return traced_atk


def run_bo3_traced(deck1: str, deck2: str, seed: int) -> str:
    orig_main = EVPlayer.decide_main_phase
    orig_atk = EVPlayer.decide_attackers
    EVPlayer.decide_main_phase = _make_traced_main(orig_main)
    EVPlayer.decide_attackers = _make_traced_atk(orig_atk)
    try:
        return run_bo3(deck1, deck2, seed=seed)
    finally:
        EVPlayer.decide_main_phase = orig_main
        EVPlayer.decide_attackers = orig_atk


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    d1 = resolve_deck_name(sys.argv[1])
    d2 = resolve_deck_name(sys.argv[2])
    seed = int(sys.argv[3])
    out = sys.argv[4] if len(sys.argv) >= 5 else None
    text = run_bo3_traced(d1, d2, seed)
    if out:
        with open(out, 'w') as f:
            f.write(text)
        print(f'Wrote {out} ({len(text.splitlines())} lines)')
    else:
        print(text)


if __name__ == '__main__':
    main()
