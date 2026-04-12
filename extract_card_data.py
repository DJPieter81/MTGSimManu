#!/usr/bin/env python3
"""
extract_card_data.py — Run verbose bo3 for all matchups, extract card-level data.
Outputs card_data.json with matchup_cards + deck_cards for dashboard D object.

Usage: python3 extract_card_data.py [bo3_per_pair]  (default: 10)
"""
import json, re, sys, io
from collections import Counter, defaultdict
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS, get_all_deck_names

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10

# Archetype map for summaries
ARCHETYPES = {
    "Boros Energy": "Energy aggro", "Jeskai Blink": "Blink value",
    "Domain Zoo": "5c aggro", "Ruby Storm": "Storm combo",
    "Amulet Titan": "Titan ramp", "Goryo's Reanimator": "Reanimator combo",
    "Mono-Black Coffers": "Mono-B control", "Eldrazi Tron": "Eldrazi ramp",
    "Living End": "Cascade combo", "UW Control": "Draw-go control",
    "Murktide Regent": "Tempo", "Dimir Murktide": "Tempo",
    "Hardened Scales": "Artifact aggro", "Grinding Station": "Artifact combo",
    "Kappa Cannoneer": "Artifact tempo",
}


def split_card_list(text, known_cards):
    """Split a comma-separated list of card names, handling names with commas.
    Uses known_cards (set of full card names) to greedily match."""
    if not known_cards:
        # Fallback: naive split
        return [c.strip() for c in text.split(',')]

    result = []
    remaining = text.strip()
    while remaining:
        remaining = remaining.lstrip(', ')
        if not remaining:
            break
        # Try to match longest known card name first
        best = None
        for card in known_cards:
            if remaining.startswith(card):
                if best is None or len(card) > len(best):
                    best = card
        if best:
            result.append(best)
            remaining = remaining[len(best):]
        else:
            # Unknown card (token etc) — take up to next comma
            # But be careful: might be "Warrior Token, Warrior Token"
            parts = remaining.split(',', 1)
            result.append(parts[0].strip())
            remaining = parts[1] if len(parts) > 1 else ''
    return result


def parse_damage_from_log(game_log, d1, d2):
    """Parse per-card damage from game log lines.

    Patterns:
    - [Declare Attackers] P{n} attacks with: Card1, Card2
      [Combat Damage] N damage dealt → ...
    - T{n} P{n}: CardName deals N damage (opponent life: X)
    - T{n} P{n}: CardName: N damage to opponent
    - T{n} P{n}: Ral ... [-2] -> Ral deals N damage ...
    """
    # Build known card set from both decks
    known_cards = set()
    for dname in [d1, d2]:
        if dname in MODERN_DECKS:
            for card in MODERN_DECKS[dname].get('mainboard', {}):
                known_cards.add(card)
            for card in MODERN_DECKS[dname].get('sideboard', {}):
                known_cards.add(card)

    damage = {d1: Counter(), d2: Counter()}
    current_attackers = []
    current_attacker_deck = None
    deck_by_player = {}

    for line in game_log:
        # Map P1/P2 to deck names from turn headers
        m = re.match(r'╔══ TURN \d+ — (.+?) \((P[12])\)', line)
        if m:
            deck_by_player[m.group(2)] = m.group(1)

        # Declare Attackers with creatures
        m = re.match(r'\s*\[Declare Attackers\] (P[12]) attacks with: (.+)', line)
        if m:
            pid = m.group(1)
            current_attacker_deck = deck_by_player.get(pid)
            current_attackers = split_card_list(m.group(2), known_cards)
            continue

        # Combat Damage — attribute to attackers
        m = re.search(r'\[Combat Damage\] (\d+) damage dealt', line)
        if m and current_attackers and current_attacker_deck:
            total_dmg = int(m.group(1))
            # Distribute evenly (approximation)
            per_creature = total_dmg / len(current_attackers)
            for creature in current_attackers:
                damage[current_attacker_deck][creature] += per_creature
            current_attackers = []
            current_attacker_deck = None
            continue

        # Direct damage: "CardName deals N damage (opponent life: X)"
        m = re.match(r'\s*T\d+ P(\d+): (.+?) deals (\d+) damage', line)
        if m:
            pid = f"P{m.group(1)}"
            card = m.group(2).strip()
            dmg = int(m.group(3))
            deck = deck_by_player.get(pid)
            if deck:
                damage[deck][card] += dmg
            continue

        # Direct damage: "CardName: N damage to opponent"
        m = re.match(r'\s*T\d+ P(\d+): (.+?): (\d+) damage to opponent', line)
        if m:
            pid = f"P{m.group(1)}"
            card = m.group(2).strip()
            dmg = int(m.group(3))
            deck = deck_by_player.get(pid)
            if deck:
                damage[deck][card] += dmg
            continue

        # Planeswalker damage: "Ral ... [-2] -> Ral deals N damage"
        m = re.search(r'T\d+ P(\d+): (.+?) \[-?\d+\] -> .+ deals (\d+) damage', line)
        if m:
            pid = f"P{m.group(1)}"
            card = m.group(2).strip()
            dmg = int(m.group(3))
            deck = deck_by_player.get(pid)
            if deck:
                damage[deck][card] += dmg
            continue

    # Round fractional damage
    for d in [d1, d2]:
        damage[d] = Counter({k: round(v) for k, v in damage[d].items() if round(v) > 0})
    return damage


def parse_sideboard_from_stderr(stderr_text, d1, d2):
    """Parse sideboard swap lines from stderr.
    Format: Sideboard (Deck vs Opp): +1 CardA, -1 CardB
    """
    sb = {d1: [], d2: []}
    for line in stderr_text.strip().split('\n'):
        m = re.match(r'\s*Sideboard \((.+?) vs (.+?)\): (.+)', line)
        if m:
            deck = m.group(1)
            swaps = m.group(3)
            if deck not in sb:
                continue
            ins = []
            outs = []
            for swap in swaps.split(','):
                swap = swap.strip()
                sm = re.match(r'([+-])(\d+) (.+)', swap)
                if sm:
                    sign, count, card = sm.group(1), sm.group(2), sm.group(3)
                    if sign == '+':
                        ins.append(f"IN: {count}x {card}")
                    else:
                        outs.append(f"OUT: {count}x {card}")
            sb[deck] = ins + outs
    return sb


def extract_from_match(runner, d1, d2, seed, verbose=True):
    """Run a Bo3 match and extract card data from game logs."""
    import random
    d1_data = MODERN_DECKS[d1]
    d2_data = MODERN_DECKS[d2]

    # Capture stderr for sideboard info
    old_stderr = sys.stderr
    sys.stderr = captured_stderr = io.StringIO()
    try:
        random.seed(seed)
        result = runner.run_match(d1, d1_data, d2, d2_data, verbose=verbose)
    finally:
        sys.stderr = old_stderr
    sb_text = captured_stderr.getvalue()

    casts = {d1: Counter(), d2: Counter()}
    damage = {d1: Counter(), d2: Counter()}
    kill_cards = {d1: Counter(), d2: Counter()}
    total_turns = 0
    g1_winner = None
    sweeps = [0, 0]
    went_to_3 = 0
    comebacks = [0, 0]
    g_winners = []
    kill_turns = {d1: [], d2: []}

    for gi, game in enumerate(result.games):
        total_turns += game.turns
        g_winners.append(game.winner_deck)

        # Parse casts
        for line in game.game_log:
            m = re.match(r'T\d+ P(\d+): Cast (.+?)(?:\s*\(|$)', line)
            if m:
                pidx = int(m.group(1))
                card = m.group(2).strip()
                pname = game.deck1_name if pidx == 1 else game.deck2_name
                casts[pname][card] += 1

        # Parse per-card damage
        game_dmg = parse_damage_from_log(game.game_log, d1, d2)
        for d in [d1, d2]:
            damage[d].update(game_dmg[d])

        # Track game winner's key card + kill turn
        if game.winner_deck:
            winner_casts = Counter()
            for line in game.game_log:
                m = re.match(r'T\d+ P(\d+): Cast (.+?)(?:\s*\(|$)', line)
                if m:
                    pidx = int(m.group(1))
                    pname = game.deck1_name if pidx == 1 else game.deck2_name
                    if pname == game.winner_deck:
                        winner_casts[m.group(2).strip()] += 1
            if winner_casts:
                top = winner_casts.most_common(1)[0][0]
                kill_cards[game.winner_deck][top] += 1
            kill_turns[game.winner_deck].append(game.turns)

    # Sideboard parsing
    sb = parse_sideboard_from_stderr(sb_text, d1, d2)

    # Series stats
    if len(g_winners) == 2 and g_winners[0] == g_winners[1]:
        idx = 0 if g_winners[0] == d1 else 1
        sweeps[idx] += 1
    if len(g_winners) == 3:
        went_to_3 = 1
        if g_winners[0] != g_winners[2]:
            idx = 0 if g_winners[2] == d1 else 1
            comebacks[idx] += 1

    g1_wins = [0, 0]
    if g_winners and g_winners[0] == d1:
        g1_wins[0] = 1
    elif g_winners and g_winners[0] == d2:
        g1_wins[1] = 1

    return {
        "casts": casts, "damage": damage, "kill_cards": kill_cards,
        "turns": total_turns, "games": len(result.games),
        "g1_wins": g1_wins, "sweeps": sweeps,
        "went_to_3": went_to_3, "comebacks": comebacks,
        "d1_won": 1 if result.winner_deck == d1 else 0,
        "d2_won": 1 if result.winner_deck == d2 else 0,
        "sb": sb, "kill_turns": kill_turns,
    }


def generate_insight(d1, d2, d1_wins, d2_wins, avg_t, sweeps, went_pct, g1_pct, comebacks, N):
    """Auto-generate matchup insight narrative."""
    total = d1_wins + d2_wins
    if total == 0:
        return "No data."

    d1_wr = round(d1_wins / total * 100)
    d2_wr = 100 - d1_wr

    # Determine dominant deck
    if d1_wr >= 55:
        dominant, dom_wr = d1, d1_wr
        underdog = d2
    elif d2_wr >= 55:
        dominant, dom_wr = d2, d2_wr
        underdog = d1
    else:
        dominant = None

    parts = []
    if dominant:
        if dom_wr >= 80:
            parts.append(f"{dominant} crushes this at {dom_wr}-{100-dom_wr}.")
        elif dom_wr >= 65:
            parts.append(f"{dominant} dominates at {dom_wr}-{100-dom_wr}.")
        else:
            parts.append(f"{dominant} favored at {dom_wr}-{100-dom_wr}.")
    else:
        parts.append(f"Even matchup at {d1_wr}-{d2_wr}.")

    # Speed
    if avg_t <= 6:
        parts.append(f"Lightning fast ({avg_t}t avg).")
    elif avg_t <= 9:
        parts.append(f"Mid-speed games ({avg_t}t avg).")
    else:
        parts.append(f"Grindy ({avg_t}t avg).")

    # Sweep rate
    sweep_total = sweeps[0] + sweeps[1]
    if total > 0:
        sweep_pct = round(sweep_total / total * 100)
        if sweep_pct >= 60:
            parts.append(f"Polarized — {sweep_pct}% sweeps.")
        elif sweep_pct <= 20 and went_pct >= 50:
            parts.append(f"Competitive — {went_pct}% go to G3.")

    # Comebacks
    cb_total = comebacks[0] + comebacks[1]
    if cb_total >= 2:
        parts.append(f"{cb_total} comebacks in {total} matches.")

    return " ".join(parts)


def generate_finisher_desc(card, deck, avg_kill_turn, is_burn=False):
    """Generate a description for a finisher card."""
    arch = ARCHETYPES.get(deck, "")
    if is_burn or any(w in card.lower() for w in ["grapeshot", "galvanic", "bolt", "phlage"]):
        return f"Direct damage finisher (avg kill T{avg_kill_turn})"
    if any(w in card.lower() for w in ["titan", "emrakul", "ulamog", "primeval"]):
        return f"Haymaker finisher — drops and ends games (T{avg_kill_turn})"
    if any(w in card.lower() for w in ["murktide", "cannoneer", "regent"]):
        return f"Evasive threat that closes fast (T{avg_kill_turn})"
    if "combo" in arch.lower():
        return f"Combo kill piece (T{avg_kill_turn})"
    return f"Primary combat closer (T{avg_kill_turn})"


def generate_deck_summary(deck, wr, meta_share, top_finishers, avg_kill_turn):
    """Auto-generate deck summary text."""
    arch = ARCHETYPES.get(deck, "Unknown archetype")
    parts = []

    if wr >= 55:
        parts.append(f"Apex predator: {wr}% weighted WR ({meta_share}% meta).")
    elif wr >= 50:
        parts.append(f"Solid performer: {wr}% WR ({meta_share}% meta).")
    elif wr >= 45:
        parts.append(f"Middle of pack: {wr}% WR ({meta_share}% meta).")
    else:
        parts.append(f"Struggling: {wr}% WR ({meta_share}% meta).")

    parts.append(f"{arch}.")
    if top_finishers:
        fin_names = [f["card"] for f in top_finishers[:2]]
        parts.append(f"Kills with {', '.join(fin_names)} (avg T{avg_kill_turn}).")
    return " ".join(parts)


def main():
    print(f"Loading card database...", file=sys.stderr)
    db = CardDatabase('ModernAtomic.json')
    runner = GameRunner(db)

    decks = get_all_deck_names()
    n = len(decks)
    total_pairs = n * (n - 1) // 2
    print(f"Running {total_pairs} matchups x {N} verbose bo3 = {total_pairs * N} matches", file=sys.stderr)

    # Per-deck accumulators
    all_casts = {d: Counter() for d in decks}
    all_damage = {d: Counter() for d in decks}
    all_kills = {d: Counter() for d in decks}
    all_kill_turns = {d: [] for d in decks}
    deck_wins = {d: 0 for d in decks}
    deck_matches = {d: 0 for d in decks}

    matchup_cards = {}
    pair_num = 0

    for i in range(n):
        for j in range(i + 1, n):
            pair_num += 1
            d1, d2 = decks[i], decks[j]

            mc = Counter()
            oc = Counter()
            md = Counter()
            od = Counter()
            mk = Counter()
            ok = Counter()
            total_turns = 0
            total_games = 0
            g1_wins = [0, 0]
            sweeps = [0, 0]
            went_to_3 = 0
            comebacks = [0, 0]
            d1_wins = 0
            d2_wins = 0
            all_sb = {d1: [], d2: []}
            kill_turns_d1 = []
            kill_turns_d2 = []

            for g in range(N):
                seed = 70000 + pair_num * 100 + g * 7
                try:
                    data = extract_from_match(runner, d1, d2, seed)
                    mc.update(data["casts"][d1])
                    oc.update(data["casts"][d2])
                    md.update(data["damage"][d1])
                    od.update(data["damage"][d2])
                    mk.update(data["kill_cards"][d1])
                    ok.update(data["kill_cards"][d2])
                    total_turns += data["turns"]
                    total_games += data["games"]
                    g1_wins[0] += data["g1_wins"][0]
                    g1_wins[1] += data["g1_wins"][1]
                    sweeps[0] += data["sweeps"][0]
                    sweeps[1] += data["sweeps"][1]
                    went_to_3 += data["went_to_3"]
                    comebacks[0] += data["comebacks"][0]
                    comebacks[1] += data["comebacks"][1]
                    d1_wins += data["d1_won"]
                    d2_wins += data["d2_won"]
                    # Merge SB (keep unique)
                    for swap in data["sb"].get(d1, []):
                        if swap not in all_sb[d1]:
                            all_sb[d1].append(swap)
                    for swap in data["sb"].get(d2, []):
                        if swap not in all_sb[d2]:
                            all_sb[d2].append(swap)
                    kill_turns_d1.extend(data["kill_turns"].get(d1, []))
                    kill_turns_d2.extend(data["kill_turns"].get(d2, []))
                except Exception as e:
                    print(f"    ERROR: {d1} vs {d2} seed {seed}: {e}", file=sys.stderr)

            all_casts[d1].update(mc)
            all_casts[d2].update(oc)
            all_damage[d1].update(md)
            all_damage[d2].update(od)
            all_kills[d1].update(mk)
            all_kills[d2].update(ok)
            all_kill_turns[d1].extend(kill_turns_d1)
            all_kill_turns[d2].extend(kill_turns_d2)
            deck_wins[d1] += d1_wins
            deck_wins[d2] += d2_wins
            deck_matches[d1] += d1_wins + d2_wins
            deck_matches[d2] += d1_wins + d2_wins

            def top(c, n=3):
                return [{"card": k, "count": v} for k, v in c.most_common(n)]

            avg_t = round(total_turns / max(total_games, 1), 1)
            g1_pct = [round(g1_wins[0] / max(N, 1) * 100), round(g1_wins[1] / max(N, 1) * 100)]
            went_pct = round(went_to_3 / max(N, 1) * 100)

            # Generate insight
            insight = generate_insight(d1, d2, d1_wins, d2_wins, avg_t, sweeps, went_pct, g1_pct, comebacks, N)

            # Finisher descriptions
            d1_avg_kt = round(sum(kill_turns_d1) / max(len(kill_turns_d1), 1), 1)
            d2_avg_kt = round(sum(kill_turns_d2) / max(len(kill_turns_d2), 1), 1)
            d1_finishers = []
            for k, v in mk.most_common(2):
                d1_finishers.append({"card": k, "count": v, "desc": generate_finisher_desc(k, d1, d1_avg_kt)})
            d2_finishers = []
            for k, v in ok.most_common(2):
                d2_finishers.append({"card": k, "count": v, "desc": generate_finisher_desc(k, d2, d2_avg_kt)})

            # SB with cast counts
            d1_sb_with_casts = list(all_sb[d1])
            d2_sb_with_casts = list(all_sb[d2])
            # Add "SB cards seen" line
            sb_cards_d1 = [s.replace("IN: ", "").split("x ", 1)[-1] for s in all_sb[d1] if s.startswith("IN:")]
            sb_seen_d1 = [(c, mc.get(c, 0) + oc.get(c, 0)) for c in sb_cards_d1 if mc.get(c, 0) + oc.get(c, 0) > 0]
            if sb_seen_d1:
                d1_sb_with_casts.append("SB cards seen: " + ", ".join(f"{c} ({n}x cast)" for c, n in sb_seen_d1))
            sb_cards_d2 = [s.replace("IN: ", "").split("x ", 1)[-1] for s in all_sb[d2] if s.startswith("IN:")]
            sb_seen_d2 = [(c, oc.get(c, 0) + mc.get(c, 0)) for c in sb_cards_d2 if oc.get(c, 0) + mc.get(c, 0) > 0]
            if sb_seen_d2:
                d2_sb_with_casts.append("SB cards seen: " + ", ".join(f"{c} ({n}x cast)" for c, n in sb_seen_d2))

            matchup_cards[f"{i},{j}"] = {
                "d1": d1, "d2": d2,
                "d1_wins": d1_wins, "d2_wins": d2_wins,
                "avg_turns": avg_t,
                "win_conditions": {"damage": total_games},
                "sweeps": sweeps,
                "went_to_3": went_pct,
                "g1_wins": g1_pct,
                "comebacks": comebacks,
                "d1_top_casts": top(mc),
                "d2_top_casts": top(oc),
                "d1_top_damage": top(md, 2),
                "d2_top_damage": top(od, 2),
                "d1_finishers": d1_finishers,
                "d2_finishers": d2_finishers,
                "insight": insight,
                "d1_sb": d1_sb_with_casts,
                "d2_sb": d2_sb_with_casts,
            }

            print(f"  [{pair_num}/{total_pairs}] {d1} vs {d2}: {d1_wins}-{d2_wins}, avg {avg_t}t, {len(mc)} unique cards, ins={insight[:60]}", file=sys.stderr)

    # Build deck_cards with summaries
    # Load meta shares if available
    try:
        with open('decks/metagame.json') as f:
            meta = json.load(f)
    except Exception:
        meta = {}

    deck_cards = []
    for i, d in enumerate(decks):
        wr = round(deck_wins[d] / max(deck_matches[d], 1) * 100, 1)
        # metagame.json values are already percentages
        meta_share = round(meta.get(d, 100.0 / len(decks)), 1)
        avg_kt = round(sum(all_kill_turns[d]) / max(len(all_kill_turns[d]), 1), 1)

        finishers = []
        for k, v in all_kills[d].most_common(4):
            finishers.append({"card": k, "count": v, "desc": generate_finisher_desc(k, d, avg_kt)})

        summary = generate_deck_summary(d, wr, meta_share, finishers, avg_kt)

        deck_cards.append({
            "deck": d, "idx": i,
            "mvp_casts": [{"card": k, "count": v} for k, v in all_casts[d].most_common(5)],
            "mvp_damage": [{"card": k, "count": v} for k, v in all_damage[d].most_common(3)],
            "finishers": finishers,
            "summary": summary,
        })

    with open('card_data.json', 'w') as f:
        json.dump({"matchup_cards": matchup_cards, "deck_cards": deck_cards}, f, indent=2)

    print(f"\nSaved card_data.json: {len(matchup_cards)} matchups, {len(deck_cards)} decks", file=sys.stderr)

    # Quick stats
    insights_filled = sum(1 for v in matchup_cards.values() if v["insight"])
    sb_filled = sum(1 for v in matchup_cards.values() if v["d1_sb"] or v["d2_sb"])
    summaries_filled = sum(1 for d in deck_cards if d["summary"])
    descs_filled = sum(1 for v in matchup_cards.values() for f in v["d1_finishers"] + v["d2_finishers"] if f.get("desc"))
    dmg_filled = sum(1 for d in deck_cards if d["mvp_damage"])
    print(f"  Insights: {insights_filled}/{len(matchup_cards)}", file=sys.stderr)
    print(f"  SB data: {sb_filled}/{len(matchup_cards)}", file=sys.stderr)
    print(f"  Summaries: {summaries_filled}/{len(deck_cards)}", file=sys.stderr)
    print(f"  Finisher descs: {descs_filled}", file=sys.stderr)
    print(f"  Damage data: {dmg_filled}/{len(deck_cards)}", file=sys.stderr)


if __name__ == '__main__':
    main()
