#!/usr/bin/env python3
"""
Merge metagame_results.json into metagame_data.jsx

Reads new win data from metagame_results.json and merges into metagame_data.jsx:
- Updates D.wins[i][j] matrix
- Updates D.matches_per_pair
- Recomputes D.overall[*].{win_rate, total_wins, total_matches}
- Preserves D.matchup_cards, D.deck_cards, D.meta_shares
"""

import json
import re

# Read metagame_results.json
with open('metagame_results.json') as f:
    results = json.load(f)

# Read metagame_data.jsx
with open('metagame_data.jsx') as f:
    jsx_content = f.read()

# Extract D object (between "const D = " and ";\nconst N")
d_match = re.search(r'const D = ({.*?});\s*\nconst N', jsx_content, re.DOTALL)
if not d_match:
    raise ValueError("Could not find D object in metagame_data.jsx")

d_json_str = d_match.group(1)
D = json.loads(d_json_str)

# Map results names to D.decks indices
results_names = results['names']
D_decks = D['decks']

print(f"Results has {len(results_names)} decks: {results_names}")
print(f"D has {len(D_decks)} decks: {D_decks}")

# Create mapping from results name to D index
name_to_idx = {name: i for i, name in enumerate(D_decks)}

# Initialize new wins matrix
N = len(D_decks)
new_wins = [[0] * N for _ in range(N)]

# Fill in new_wins from results.matrix
# Each key is "Deck A|Deck B" with win count
for key, win_count in results['matrix'].items():
    d1, d2 = key.split('|')
    i = name_to_idx.get(d1)
    j = name_to_idx.get(d2)
    if i is None or j is None:
        print(f"WARNING: Could not map {key} to indices")
        continue
    new_wins[i][j] = win_count

# Update D.wins and matches_per_pair
D['wins'] = new_wins
D['matches_per_pair'] = results['n_games']

# Recompute D.overall stats
# wins[i][j] is count of games deck i won vs deck j out of 100 games (50 BO3 matches)
# total_wins = sum of wins[i][j] for all j!=i
# total_matches = (N-1) * 100 (each matchup has 100 game instances)
# win_rate = total_wins / total_matches * 100

for deck_entry in D['overall']:
    idx = deck_entry['idx']
    total_wins = sum(D['wins'][idx][j] for j in range(N) if j != idx)
    # Each matchup has 100 game instances (50 BO3 matches with ~2 games each on avg)
    total_matches = (N - 1) * 100
    win_rate = (total_wins / total_matches * 100) if total_matches > 0 else 0

    deck_entry['win_rate'] = round(win_rate, 1)
    deck_entry['total_wins'] = total_wins
    deck_entry['total_matches'] = total_matches

    # For weighted_wr: if meta_shares exists, compute weighted average
    # Otherwise set equal to win_rate
    if 'meta_shares' in D:
        # Compute weighted WR: sum(wr_vs_i * meta_share_i) for all i!=idx
        weighted_wins = 0
        weighted_matches = 0
        for j in range(N):
            if j != idx:
                deck_j = D['decks'][j]
                meta_share_j = D['meta_shares'].get(deck_j, 0) / 100  # convert % to decimal
                # wins[idx][j] is out of 100 games
                weighted_wins += D['wins'][idx][j] * meta_share_j
                weighted_matches += 100 * meta_share_j

        weighted_wr = (weighted_wins / weighted_matches * 100) if weighted_matches > 0 else win_rate
        deck_entry['weighted_wr'] = round(weighted_wr, 1)
    else:
        deck_entry['weighted_wr'] = round(win_rate, 1)

# Convert D back to JSON string and write out
d_json_out = json.dumps(D, separators=(',', ':'))
new_jsx = f"const D = {d_json_out};\nconst N = {N};"

with open('metagame_data.jsx', 'w') as f:
    f.write(new_jsx)

# Verify
print(f"\nMerge complete!")
print(f"Updated metagame_data.jsx with:")
print(f"  - {N} decks")
print(f"  - {D['matches_per_pair']} games per matchup")
print(f"\nDeck win rates:")
for entry in sorted(D['overall'], key=lambda x: x['win_rate'], reverse=True):
    print(f"  {entry['deck']}: {entry['win_rate']}% (flat) / {entry['weighted_wr']}% (weighted)")
