"""The turn cap is a wall-clock valve, not a tiebreak.

`MAX_TURNS` exists so a pathological game cannot run forever. It counts
HALF-turns, so at 25 it used to bind around display turn 12-13 — and at the
cap `GameRunner` awarded the win to whoever had more LIFE.

That is not a Magic result. It is the harness adjudicating an unfinished
game with a metric that correlates almost perfectly with being the beatdown:
an aggro deck has spent the game reducing its opponent's total and typically
taking little back, so it wins the comparison close to automatically, while
a control deck that stabilised low but assembled an unbeatable board loses.

Measured before this change (Bo1, offline scorer):

    Domain Zoo vs 8 mixed opponents, 32 games :  2/32 =  6% capped
    Domain Zoo vs Azorius Control,   40 games : 13/40 = 32% capped

and lifting the cap moved the CONTROL decks (4/5c Control 34.4 -> 50.0,
into band) while the aggro decks stayed flat. Full write-up:
docs/diagnostics/2026-08-30_turn_cap_deflates_control.md.

Two rules are pinned here:

  * An unfinished game is a DRAW (CR 104.4 — a game that does not end with a
    winner is a draw). Life total does not break it.
  * A draw is not a win for anybody. The Bo3 scorer used to count
    `winner_deck` against `deck1` and give EVERY other value — including the
    literal string "draw" — to deck2, so a drawn game silently became a game
    win for whichever deck happened to be second. That made the draw path
    strictly worse than the life tiebreak it replaces, which is why it is
    fixed in the same commit.

Rules-phrased; deck names are fixture carriers only.
"""
from __future__ import annotations

from engine.constants import MAX_TURNS


def test_the_turn_cap_is_high_enough_that_real_games_finish_first():
    """The valve must sit ABOVE the length of a normal game, or it stops
    being a safety valve and becomes the thing that decides matches.

    Measured on a control-heavy sample (24 Bo1 games): at 25 half-turns 10
    games were adjudicated, at 40 four were, and at 60 none were. 80 and 120
    were also zero, so 60 is the smallest value that buys the whole effect.
    """
    assert MAX_TURNS >= 60, (
        f"MAX_TURNS={MAX_TURNS} counts HALF-turns, so it binds around "
        f"display turn {MAX_TURNS // 2}. Below 60 the cap adjudicates real "
        f"games — measured 10/24 on a control-heavy sample at 25."
    )


def test_an_unfinished_game_is_a_draw_not_a_win_on_life_total():
    """CR 104.4 — a game that does not end with a winner is a draw.

    The adjudication is exercised through the same helper the runner uses,
    so the rule is pinned at the point of decision rather than by driving a
    full game to the cap (which would be slow and seed-dependent).
    """
    from engine.game_runner import adjudicate_capped_game

    # Wildly unequal life totals must NOT produce a winner.
    assert adjudicate_capped_game(life=[20, 1]) is None
    assert adjudicate_capped_game(life=[1, 20]) is None
    assert adjudicate_capped_game(life=[7, 7]) is None


def test_a_drawn_game_is_not_scored_as_a_win_for_either_deck():
    """The Bo3 scorer must not hand a draw to whichever deck is `deck2`.

    Regression: the loop read `if winner_deck == deck1: score[0] += 1 else:
    score[1] += 1`, and `winner_deck` is the literal string "draw" for a
    drawn game, so every draw became a game win for deck2.
    """
    from run_meta import score_game_result

    assert score_game_result("Deck A", "Deck A", "Deck B") == (1, 0, 0)
    assert score_game_result("Deck B", "Deck A", "Deck B") == (0, 1, 0)
    assert score_game_result("draw", "Deck A", "Deck B") == (0, 0, 1)


def test_a_match_of_repeated_draws_terminates():
    """A Bo3 loop gated only on `score[0] < 2 and score[1] < 2` never exits
    if games stop incrementing either score. The bound must be on GAMES
    PLAYED, not on the score, so an all-draws match ends instead of hanging
    the whole matrix run.
    """
    from run_meta import BO3_MAX_GAMES

    assert BO3_MAX_GAMES >= 3, "a Bo3 needs at least three games"
    assert BO3_MAX_GAMES < 100, (
        f"BO3_MAX_GAMES={BO3_MAX_GAMES} is not a bound — an all-draws match "
        f"would still dominate a matrix run"
    )
