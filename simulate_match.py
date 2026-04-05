"""Generate a best-of-3 match play-by-play and write standalone HTML.

Usage:
    from simulate_match import simulate_match
    simulate_match("Ruby Storm", "Domain Zoo", seed=55555)
    # → opens match_playbyplay_standalone.html

    # Or from command line:
    python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555
"""
import json
import os
import random
import sys
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game

# HTML viewer template path (relative to this file)
_HTML_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'match_viewer_template.html')


def _build_standalone_html(json_str: str, p1_short: str, p2_short: str) -> str:
    """Build a self-contained HTML file with embedded match data."""
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'match_viewer_template.html')
    with open(template_path) as f:
        template = f.read()
    return template.replace('/*__MATCH_DATA__*/null;/*__END_DATA__*/',
                            json_str + ';')


def _short(name):
    """Short display name for a deck."""
    parts = name.split()
    if len(parts) == 1:
        return parts[0][:6]
    # Use last word unless it's generic
    for skip in ('Midrange', 'Energy', 'Prowess', 'Tron'):
        if skip in name:
            return name.replace(skip, '').strip()[:8] or name.split()[0]
    return parts[0][:8]


def _board_cards(player):
    return [{'name': c.name.split(',')[0].split('//')[0].strip(),
             'is_creature': c.template.is_creature,
             'power': c.power, 'toughness': c.toughness}
            for c in player.battlefield if not c.template.is_land]


def _hand_cards(player):
    return [{'name': c.name.split(',')[0].split('//')[0].strip(),
             'is_land': c.template.is_land, 'cmc': c.template.cmc or 0}
            for c in player.hand]


def _describe_spell(card, tags, ev, snap, me, opp, alternatives):
    """Human-readable reasoning for why this spell is being cast."""
    t = card.template
    name = card.name.split(',')[0].split('//')[0].strip()
    storm = me.spells_cast_this_turn
    mana = me.available_mana_estimate + me.mana_pool.total()

    if storm >= 2:
        if 'ritual' in tags:
            return f"Chain fuel — ritual produces mana to keep comboing (mana: {mana}→{mana+1}+)"
        if 'cantrip' in tags or 'draw' in tags:
            return f"Chain dig — draw cards looking for Wish/finisher (storm {storm})"
        if 'tutor' in tags:
            return f"Find finisher! Storm count is {storm}, searching sideboard for Grapeshot/Empty the Warrens"
        from engine.cards import Keyword
        if Keyword.STORM in getattr(t, 'keywords', set()):
            copies = storm + 1
            if copies >= snap.opp_life:
                return f"LETHAL STORM! {copies} copies × 1 damage = {copies} vs {snap.opp_life} life"
            pct = int(copies / max(1, snap.opp_life) * 100)
            if 'token_maker' in tags:
                return f"Create {copies*2} goblin tokens (storm {storm}) — {pct}% of lethal equivalent"
            return f"Fire Grapeshot at storm {storm} — {copies} damage vs {snap.opp_life} life ({pct}% lethal)"
        if 'cost_reducer' in tags:
            return f"Deploy engine mid-chain — future spells cost less (storm {storm})"
        if 'flashback' in tags:
            gy_count = sum(1 for c in me.graveyard if c.template.is_instant or c.template.is_sorcery)
            return f"Past in Flames — rebuy {gy_count} spells from graveyard"
        return f"Continue chain (storm {storm}, mana {mana})"

    if 'cost_reducer' in tags:
        return "Deploy cost reducer — all future instants/sorceries cost 1 less"
    if 'ritual' in tags:
        return "Ritual — produces mana for combo (net +1 red mana)"
    if ev >= 100:
        return "Lethal damage — wins the game"
    if 'removal' in tags and not t.is_creature:
        if snap.opp_creature_count > 0:
            return f"Remove opponent's threat ({snap.opp_power} power on board)"
        return "Removal spell (no creature targets currently)"
    from decks.card_knowledge_loader import get_burn_damage
    dmg = get_burn_damage(t.name)
    if dmg > 0:
        if dmg >= snap.opp_life:
            return f"Burn for lethal! {dmg} damage vs {snap.opp_life} life"
        return f"Burn face for {dmg} damage (opponent at {snap.opp_life} life)"
    if t.is_creature:
        p = t.power or 0
        if snap.my_creature_count == 0:
            return f"Deploy first creature — establishes board presence ({p} power)"
        if p >= 3:
            return f"Deploy threat — {p} power creature pressures opponent"
        return f"Deploy creature ({p}/{t.toughness or 0})"
    from engine.cards import CardType
    if CardType.PLANESWALKER in t.card_types:
        return "Deploy planeswalker — generates recurring value each turn"
    if 'cantrip' in tags or 'draw' in tags:
        if snap.my_hand_size <= 2:
            return "Draw cards — hand is nearly empty, need gas"
        return "Draw cards — dig for key spells"
    if 'discard' in tags:
        return "Strip best card from opponent's hand"
    if 'board_wipe' in tags:
        return f"Board wipe — destroy all {snap.opp_creature_count} opponent creatures"
    return "Best available play"


def simulate_match(deck1_name: str, deck2_name: str, seed: int = None,
                   output: str = 'match_playbyplay_standalone.html'):
    """Simulate a best-of-3 match and write a standalone HTML play-by-play.

    Args:
        deck1_name: Name of deck 1 (must be in MODERN_DECKS)
        deck2_name: Name of deck 2 (must be in MODERN_DECKS)
        seed: Random seed for reproducibility (None = random)
        output: Output HTML file path

    Returns:
        dict with match result info
    """
    if deck1_name not in MODERN_DECKS:
        available = ', '.join(sorted(MODERN_DECKS.keys()))
        raise ValueError(f"Unknown deck '{deck1_name}'. Available: {available}")
    if deck2_name not in MODERN_DECKS:
        available = ', '.join(sorted(MODERN_DECKS.keys()))
        raise ValueError(f"Unknown deck '{deck2_name}'. Available: {available}")

    if seed is None:
        seed = random.randint(10000, 99999)

    db = CardDatabase()
    runner = GameRunner(db)
    d1, d2 = MODERN_DECKS[deck1_name], MODERN_DECKS[deck2_name]

    p1_short = _short(deck1_name)
    p2_short = _short(deck2_name)

    orig_main = EVPlayer.decide_main_phase
    orig_atk = EVPlayer.decide_attackers
    games_data = []
    current_game = []

    def log_main(self, game, excluded=None):
        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        snap = snapshot_from_game(game, self.player_idx)
        result = orig_main(self, game, excluded)
        if not result:
            return result

        action, card, targets = result
        card_short = card.name.split(',')[0].split('//')[0].strip()
        is_p1 = me.deck_name == deck1_name
        p1_p = me if is_p1 else opp
        p2_p = opp if is_p1 else me

        row = {
            'turn': game.turn_number,
            'who': p1_short if is_p1 else p2_short,
            'who_idx': 0 if is_p1 else 1,
            'p1_life': p1_p.life, 'p2_life': p2_p.life,
            'p1_board': _board_cards(p1_p), 'p2_board': _board_cards(p2_p),
            'p1_lands': len(p1_p.lands), 'p2_lands': len(p2_p.lands),
            'hand': _hand_cards(me), 'hand_size': len(me.hand),
        }

        tags = getattr(card.template, 'tags', set())
        storm_count = me.spells_cast_this_turn
        mana = me.available_mana_estimate + me.mana_pool.total()

        if action == 'play_land':
            tapped = card.template.enters_tapped
            row['action'] = f"Play land: {card_short}"
            row['action_type'] = 'land'
            row['reasoning'] = f"{'Enters tapped' if tapped else 'Untapped'} — adds mana"
        elif action == 'cast_spell':
            ev = self._score_spell(card, snap, game, me, opp)
            target_str = ''
            if targets:
                for t_id in targets:
                    if t_id == -1:
                        target_str = ' → opponent'
                    else:
                        tc = game.get_card_by_id(t_id)
                        if tc:
                            target_str = f' → {tc.name.split(",")[0]}'

            chain_prefix = f'[Chain #{storm_count+1}] ' if storm_count >= 2 else ''
            row['action'] = f"{chain_prefix}Cast {card_short}{target_str}"
            row['action_type'] = 'spell'
            row['ev'] = round(ev, 1)
            row['storm_count'] = storm_count
            row['mana_after'] = max(0, mana - (card.template.cmc or 0))
            row['mana_pool'] = me.mana_pool.total()

            alts = []
            for c in me.hand:
                if c.instance_id == card.instance_id or c.template.is_land:
                    continue
                if game.can_cast(self.player_idx, c):
                    aev = self._score_spell(c, snap, game, me, opp)
                    alts.append({'name': c.name.split(',')[0].split('//')[0].strip(),
                                 'ev': round(aev, 1)})
            alts.sort(key=lambda x: -x['ev'])
            row['alternatives'] = alts[:3]
            row['reasoning'] = _describe_spell(card, tags, ev, snap, me, opp, alts)

        current_game.append(row)
        return result

    def log_atk(self, game):
        result = orig_atk(self, game)
        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        if not result:
            return result

        power = sum(c.power or 0 for c in result)
        names = [c.name.split(',')[0].split('//')[0].strip() for c in result]
        lethal = power >= opp.life
        is_p1 = me.deck_name == deck1_name
        p1_p = me if is_p1 else opp
        p2_p = opp if is_p1 else me

        if lethal:
            reason = f"Lethal! {power} damage kills opponent at {opp.life} life"
        elif power >= opp.life * 0.5:
            reason = f"Big attack — {power} damage is {int(power / opp.life * 100)}% of opponent's {opp.life} life"
        else:
            reason = f"Chip away — {power} damage vs {opp.life} life"

        current_game.append({
            'turn': game.turn_number,
            'who': p1_short if is_p1 else p2_short,
            'who_idx': 0 if is_p1 else 1,
            'p1_life': p1_p.life, 'p2_life': p2_p.life,
            'p1_board': _board_cards(p1_p), 'p2_board': _board_cards(p2_p),
            'hand': [], 'hand_size': len(me.hand),
            'action': f"Attack with {', '.join(names)} for {power}",
            'action_type': 'attack',
            'reasoning': reason,
        })
        return result

    EVPlayer.decide_main_phase = log_main
    EVPlayer.decide_attackers = log_atk

    random.seed(seed)
    match_score = [0, 0]

    for game_num in range(1, 4):
        current_game = []
        r = runner.run_game(
            deck1_name, d1['mainboard'], deck2_name, d2['mainboard'],
            deck1_sideboard=d1.get('sideboard', {}),
            deck2_sideboard=d2.get('sideboard', {}))
        match_score[0 if r.winner == 0 else 1] += 1
        games_data.append({
            'game_num': game_num, 'winner': r.winner_deck,
            'turns': r.turns, 'win_condition': r.win_condition,
            'p1_spells': r.deck1_spells_cast, 'p2_spells': r.deck2_spells_cast,
            'p1_final_life': r.winner_life if r.winner == 0 else r.loser_life,
            'p2_final_life': r.winner_life if r.winner == 1 else r.loser_life,
            'rows': current_game, 'score': list(match_score),
        })
        if match_score[0] >= 2 or match_score[1] >= 2:
            break

    EVPlayer.decide_main_phase = orig_main
    EVPlayer.decide_attackers = orig_atk

    match_data = {
        'deck1': deck1_name, 'deck2': deck2_name,
        'p1_short': p1_short, 'p2_short': p2_short,
        'games': games_data,
        'match_winner': deck1_name if match_score[0] > match_score[1] else deck2_name,
        'match_score': match_score,
        'seed': seed,
    }

    # Build standalone HTML directly (no template — avoids escaping issues)
    json_str = json.dumps(match_data)
    html = _build_standalone_html(json_str, p1_short, p2_short)

    with open(output, 'w') as f:
        f.write(html)

    winner_short = _short(match_data['match_winner'])
    print(f"Match: {deck1_name} vs {deck2_name} (seed {seed})")
    for g in games_data:
        print(f"  Game {g['game_num']}: {g['winner']} T{g['turns']} ({len(g['rows'])} actions)")
    print(f"  Winner: {match_data['match_winner']} {match_score[0]}-{match_score[1]}")
    print(f"  → {output}")

    return match_data


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Simulate a best-of-3 MTG match')
    parser.add_argument('deck1', help='Deck 1 name')
    parser.add_argument('deck2', help='Deck 2 name')
    parser.add_argument('--seed', type=int, default=None, help='Random seed')
    parser.add_argument('--output', '-o', default='match_playbyplay_standalone.html',
                        help='Output HTML file')
    parser.add_argument('--list-decks', action='store_true', help='List available decks')
    args = parser.parse_args()

    if args.list_decks:
        print("Available decks:")
        for name in sorted(MODERN_DECKS.keys()):
            print(f"  {name}")
        sys.exit(0)

    simulate_match(args.deck1, args.deck2, seed=args.seed, output=args.output)
