#!/usr/bin/env python3
"""
Generate structured JSON replay data from a game for visual animation.

v2: Each snapshot now includes a structured `events` array alongside the
legacy `event` string.  Every game.log entry produced between two snapshots
is captured, parsed into a typed event dict, and attached to the snapshot.
"""
import argparse
import json
import random
import re
from typing import Dict, List, Optional, Tuple, Set

from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase, PlayerState, PLANESWALKER_ABILITIES
from engine.cards import CardTemplate, CardInstance, Keyword, CardType
from engine.stack import StackItemType
from engine.mana import ManaCost, ManaPool

import sys
sys.path.insert(0, '/home/ubuntu/mtg_simulator')
from ai.ai_player import AIPlayer
from decks.modern_meta import MODERN_DECKS


# =====================================================================
# Card / player serialisation (unchanged from v1)
# =====================================================================

def card_to_dict(c: CardInstance) -> dict:
    types = [t.value for t in c.template.card_types]
    keywords = [k.value for k in c.keywords] if c.keywords else []
    return {
        "id": c.instance_id,
        "name": c.name,
        "types": types,
        "is_creature": c.template.is_creature,
        "is_land": c.template.is_land,
        "is_instant": c.template.is_instant,
        "is_sorcery": c.template.is_sorcery,
        "is_planeswalker": any(t.lower() == "planeswalker" for t in types),
        "is_artifact": "artifact" in types,
        "is_enchantment": "enchantment" in types,
        "power": c.power if c.template.is_creature else None,
        "toughness": c.toughness if c.template.is_creature else None,
        "keywords": keywords,
        "tapped": c.tapped,
        "summoning_sick": c.has_summoning_sickness,
        "loyalty": c.loyalty_counters if any(t.lower() == "planeswalker" for t in types) else None,
        "mana_cost": format_cost(c.template.mana_cost),
        "cmc": c.template.mana_cost.cmc,
    }


def format_cost(mc: ManaCost) -> str:
    parts = []
    if mc.generic:
        parts.append(str(mc.generic))
    if mc.white:
        parts.append("W" * mc.white)
    if mc.blue:
        parts.append("U" * mc.blue)
    if mc.black:
        parts.append("B" * mc.black)
    if mc.red:
        parts.append("R" * mc.red)
    if mc.green:
        parts.append("G" * mc.green)
    return "".join(parts) if parts else "0"


def player_snapshot(p: PlayerState, name: str) -> dict:
    return {
        "name": name,
        "life": p.life,
        "energy": p.energy_counters,
        "hand_count": len(p.hand),
        "library_count": len(p.library),
        "hand": [card_to_dict(c) for c in p.hand],
        "battlefield": [card_to_dict(c) for c in p.battlefield],
        "graveyard": [card_to_dict(c) for c in p.graveyard],
        "exile": [card_to_dict(c) for c in p.exile],
    }


# =====================================================================
# Log-line parser  --  converts raw game.log strings to typed events
# =====================================================================

_RE_TP = re.compile(r'^T(\d+)\s+P(\d+):\s*(.+)$')
_RE_T  = re.compile(r'^T(\d+):\s*(.+)$')
_RE_P  = re.compile(r'^P(\d+)\s+(.+)$')


def _evt(cat, typ, player, card, text, raw, details=None):
    d = {
        "category": cat,
        "type": typ,
        "player": player,
        "card": card,
        "text": text,
        "raw": raw,
    }
    if details:
        d["details"] = details
    return d


def classify_log(raw: str) -> dict:
    """Parse one game.log line into a structured event dict."""
    line = raw.strip()
    if not line:
        return _evt("game", "empty", None, None, "", raw)

    turn = None
    player = None
    body = line

    m = _RE_TP.match(line)
    if m:
        turn, player, body = int(m.group(1)), int(m.group(2)) - 1, m.group(3)
    else:
        m2 = _RE_T.match(line)
        if m2:
            turn, body = int(m2.group(1)), m2.group(2)
        else:
            m3 = _RE_P.match(line)
            if m3:
                player, body = int(m3.group(1)) - 1, m3.group(2)

    # ── Land plays ──────────────────────────────────────────
    if body.startswith("Play "):
        card = body.split("Play ", 1)[1].split(" (")[0]
        det = {}
        if "(tapped" in body:
            det["tapped"] = True
        if "pay 2 life" in body:
            det["shock"] = True
        lm = re.search(r'life:\s*(\d+)', body)
        if lm:
            det["life_after"] = int(lm.group(1))
        return _evt("land", "play_land", player, card, body, raw, det or None)

    # ── Fetch land crack ────────────────────────────────────
    if body.startswith("Crack "):
        parts = body.split("->")
        fetch = body.split("Crack ", 1)[1].split(" (")[0].split(" ->")[0].strip()
        found = parts[1].strip().split(" (")[0].strip() if len(parts) > 1 else None
        det = {"fetch": fetch}
        if found:
            det["found"] = found
        if "pay 1 life" in body:
            det["fetch_life"] = 1
        if "pay 2 life" in body:
            det["shock_life"] = 2
        if "->" in body and "tapped" in body.split("->")[-1]:
            det["tapped"] = True
        lm = re.search(r'life:\s*(\d+)', body)
        if lm:
            det["life_after"] = int(lm.group(1))
        return _evt("land", "fetch_crack", player, fetch, body, raw, det)

    # ── Spell casting ───────────────────────────────────────
    if body.startswith("Cast "):
        card = body.split("Cast ", 1)[1].split(" (")[0].strip()
        det = {}
        if "(Dash)" in body:
            det["dash"] = True
        return _evt("spell", "cast_spell", player, card, body, raw, det or None)

    if body.startswith("Evoke "):
        card = body.split("Evoke ", 1)[1].split(" (")[0].strip()
        em = re.search(r'exile\s+(.+)\)', body)
        exiled = em.group(1) if em else None
        return _evt("spell", "evoke", player, card, body, raw, {"exiled": exiled} if exiled else None)

    if body.startswith("Escape "):
        card = body.split("Escape ", 1)[1].split(" (")[0].strip()
        return _evt("spell", "escape", player, card, body, raw)

    if body.startswith("Delve "):
        dm = re.search(r'Delve\s+(\d+)\s+cards?\s+for\s+(.+)', body)
        if dm:
            return _evt("spell", "delve", player, dm.group(2), body, raw, {"count": int(dm.group(1))})

    # ── Spell resolution ────────────────────────────────────
    if body.startswith("Resolve "):
        card = body.split("Resolve ", 1)[1].strip()
        return _evt("spell", "resolve", player, card, body, raw)

    # ── Counter ─────────────────────────────────────────────
    if "is countered" in body:
        card = body.replace(" is countered", "").strip()
        return _evt("spell", "countered", player, card, body, raw)

    # ── Planeswalker ────────────────────────────────────────
    pw_m = re.match(r'^(.+?)\s+\[([+-]\d+)\]\s*->\s*(.+)$', body)
    if pw_m:
        pw_name = pw_m.group(1)
        lc = int(pw_m.group(2))
        eff = pw_m.group(3)
        return _evt("planeswalker", "pw_activate", player, pw_name, body, raw,
                     {"loyalty_change": lc, "effect": eff})

    # PW sub-effects (Wrenn returns, Teferi bounces, etc.)
    if "Wrenn and Six returns" in body:
        rm = re.search(r'returns\s+(.+?)\s+from', body)
        card = rm.group(1) if rm else None
        return _evt("planeswalker", "pw_effect", player, card, body, raw, {"source": "Wrenn and Six"})

    if body.startswith("Prismatic Ending exiles"):
        card = body.split("exiles ")[-1].strip()
        return _evt("spell", "exile_target", player, card, body, raw, {"source": "Prismatic Ending"})

    if "Wrath of the Skies" in body and "sweeps" in body:
        xm = re.search(r'X=(\d+)', body)
        return _evt("spell", "board_wipe", player, "Wrath of the Skies", body, raw,
                     {"x_value": int(xm.group(1)) if xm else 0})

    if "Unmarked Grave puts" in body:
        rm = re.search(r'puts\s+(.+?)\s+in graveyard', body)
        card = rm.group(1) if rm else None
        return _evt("spell", "tutor_to_gy", player, card, body, raw, {"source": "Unmarked Grave"})

    if body.startswith("Wish finds"):
        card = body.split("finds ")[-1].strip()
        return _evt("spell", "tutor", player, card, body, raw, {"source": "Wish"})

    if "Gifts Ungiven finds" in body:
        cards_str = body.split("finds ")[-1].replace(" to GY", "").strip()
        return _evt("spell", "tutor_to_gy", player, None, body, raw,
                     {"source": "Gifts Ungiven", "cards": cards_str})

    if "Past in Flames grants flashback" in body:
        return _evt("spell", "flashback_grant", player, "Past in Flames", body, raw)

    if "Orim's Chant silences" in body:
        return _evt("spell", "silence", player, "Orim's Chant", body, raw)

    # ── Triggered abilities ─────────────────────────────────
    if "Omnath" in body and "landfall" in body:
        det = {}
        if "+4 life" in body:
            det["effect"] = "gain_4_life"
        elif "+RGWU" in body:
            det["effect"] = "add_mana"
        elif "4 damage" in body:
            det["effect"] = "deal_4_damage"
        return _evt("trigger", "landfall", player, "Omnath, Locus of Creation", body, raw, det)

    if "Phlage attack trigger" in body:
        return _evt("trigger", "attack_trigger", player, "Phlage, Titan of Fire's Fury", body, raw)

    if "Annihilator" in body:
        am = re.search(r'Annihilator\s+(\d+)', body)
        return _evt("trigger", "annihilator", player, None, body, raw,
                     {"count": int(am.group(1)) if am else 0})

    if "Amulet of Vigor untaps" in body:
        card = body.split("untaps ")[-1].strip()
        return _evt("trigger", "amulet_untap", player, card, body, raw)

    # ── Token creation ──────────────────────────────────────
    if "Create" in body and "token" in body:
        tm = re.search(r'Create\s+(\d+)x\s+(.+?)\s+token', body)
        if tm:
            return _evt("zone", "create_token", player, tm.group(2) + " Token", body, raw,
                         {"count": int(tm.group(1))})

    # ── Energy ──────────────────────────────────────────────
    if "energy" in body.lower():
        if "produces" in body or "+energy" in body.lower():
            em = re.search(r'(\d+)\s+energy', body)
            src = re.search(r'from\s+(.+?)\s*\(', body)
            return _evt("resource", "energy_gain", player,
                         src.group(1) if src else None, body, raw,
                         {"amount": int(em.group(1)) if em else 0})
        if "Spend" in body:
            em = re.search(r'Spend\s+(\d+)\s+energy', body)
            return _evt("resource", "energy_spend", player, None, body, raw,
                         {"amount": int(em.group(1)) if em else 0})

    # ── Equipment ───────────────────────────────────────────
    if body.startswith("Equip "):
        em = re.match(r'Equip\s+(.+?)\s+to\s+(.+?)(?:\s+\(|$)', body)
        if em:
            return _evt("zone", "equip", player, em.group(1), body, raw, {"target": em.group(2)})

    if "falls off" in body:
        fm = re.match(r'(.+?)\s+falls off\s+(.+?)\s*\(', body)
        if fm:
            return _evt("zone", "unequip", player, fm.group(1), body, raw, {"creature": fm.group(2)})

    # ── Zone transitions ────────────────────────────────────
    if body.endswith(" dies") or (body.endswith(" dies") and "sacrificed" not in body):
        card = body.replace(" dies", "").strip()
        return _evt("zone", "dies", player, card, body, raw)

    if "sacrificed" in body:
        card = body.split(" sacrificed")[0].strip()
        cause = ""
        if "(evoke)" in body:
            cause = "evoke"
        elif "(not escaped)" in body:
            cause = "not_escaped"
        return _evt("zone", "sacrifice", player, card, body, raw, {"cause": cause} if cause else None)

    if "returns (undying)" in body:
        card = body.replace(" returns (undying)", "").strip()
        return _evt("zone", "undying", player, card, body, raw)

    if "returns (persist)" in body:
        card = body.replace(" returns (persist)", "").strip()
        return _evt("zone", "persist", player, card, body, raw)

    if body.startswith("Blink "):
        card = body.split("Blink ", 1)[1].strip()
        return _evt("zone", "blink", player, card, body, raw)

    if "returned to hand (Dash)" in body:
        card = body.replace(" returned to hand (Dash)", "").strip()
        return _evt("zone", "dash_return", player, card, body, raw)

    if "exiled (end of turn)" in body:
        card = body.replace(" exiled (end of turn)", "").strip()
        return _evt("zone", "exile_eot", player, card, body, raw)

    if "moved" in body and "->" in body:
        zm = re.match(r'(.+?)\s+moved\s+(\w+)\s*->\s*(\w+)', body)
        if zm:
            return _evt("zone", "zone_change", player, zm.group(1), body, raw,
                         {"from_zone": zm.group(2), "to_zone": zm.group(3)})

    # ── Reanimate ───────────────────────────────────────────
    if body.startswith("Reanimate "):
        card = body.split("Reanimate ", 1)[1].strip()
        return _evt("zone", "reanimate", player, card, body, raw)

    # ── Living End ──────────────────────────────────────────
    if "Living End resolves" in body:
        return _evt("spell", "living_end", player, "Living End", body, raw)
    if "Living End returns" in body:
        rm = re.search(r'returns\s+(.+?)\s+for\s+P(\d+)', body)
        if rm:
            return _evt("zone", "reanimate", int(rm.group(2)) - 1, rm.group(1), body, raw,
                         {"source": "Living End"})

    # ── Storm / Cascade ─────────────────────────────────────
    if "Storm copies:" in body:
        sm = re.search(r'Storm copies:\s*(\d+)', body)
        return _evt("spell", "storm", player, None, body, raw,
                     {"copies": int(sm.group(1)) if sm else 0})

    if body.startswith("Cascade"):
        cm = re.search(r'CMC < (\d+)', body)
        return _evt("spell", "cascade", player, None, body, raw,
                     {"cmc_limit": int(cm.group(1)) if cm else 0})

    if "Cascade hits" in body:
        card = body.split("hits ")[-1].strip()
        return _evt("spell", "cascade_hit", player, card, body, raw)

    # ── Griselbrand ─────────────────────────────────────────
    if "Griselbrand: pay 7 life" in body:
        return _evt("spell", "activated_ability", player, "Griselbrand", body, raw,
                     {"life_paid": 7, "cards_drawn": 7})

    # ── Damage to creatures (PW abilities) ──────────────────
    if "deals" in body and "to" in body:
        dm = re.search(r'(.+?)\s+deals\s+(\d+)\s+to\s+(.+)', body)
        if dm:
            return _evt("combat", "direct_damage", player, dm.group(1), body, raw,
                         {"damage": int(dm.group(2)), "target": dm.group(3)})

    # Galvanic Discharge
    if "Galvanic Discharge deals" in body:
        dm = re.search(r'deals\s+(\d+)\s+to\s+(.+)', body)
        if dm:
            return _evt("spell", "direct_damage", player, "Galvanic Discharge", body, raw,
                         {"damage": int(dm.group(1)), "target": dm.group(2)})

    # ── Game over ───────────────────────────────────────────
    if "loses:" in body or "loses: life total" in body:
        return _evt("game", "player_loses", player, None, body, raw)

    # ── Mana (rituals) ──────────────────────────────────────
    if "adds" in body and "mana" in body:
        return _evt("resource", "mana_add", player, None, body, raw)

    # ── Fallback ────

    return _evt("game", "other", player, None, body, raw)


# =====================================================================
# ReplayGenerator  --  runs the game and captures structured snapshots
# =====================================================================

class ReplayGenerator:
    def __init__(self, card_db, rng):
        self.card_db = card_db
        self.rng = rng
        self.events = []
        self.snapshots = []
        self._log_cursor = 0  # tracks position in game.log

    def build_deck(self, deck_list):
        deck = []
        for card_name, count in deck_list.items():
            template = self.card_db.get_card(card_name)
            if template:
                for _ in range(count):
                    deck.append(template)
            else:
                from engine.mana import ManaCost as MC
                placeholder = CardTemplate(
                    name=card_name, card_types=[CardType.SORCERY],
                    mana_cost=MC(generic=2), tags={"placeholder"},
                )
                for _ in range(count):
                    deck.append(placeholder)
        return deck

    # ── Capture new game.log entries since last snapshot ──

    def _drain_log(self, game) -> List[dict]:
        """Return structured events for all game.log entries added since last call."""
        new_lines = game.log[self._log_cursor:]
        self._log_cursor = len(game.log)
        return [classify_log(line) for line in new_lines if line.strip()]

    # ── Snapshot creation ──

    def snapshot(self, game, names, event_text="", extra_events=None):
        """Create a snapshot with both legacy event string and structured events array."""
        # Drain any new log entries
        log_events = self._drain_log(game)

        # Merge with any explicitly-provided events
        all_events = []
        if extra_events:
            all_events.extend(extra_events)
        all_events.extend(log_events)

        s = {
            "turn": game.turn_number,
            "active_player": game.active_player,
            "phase": game.current_phase.value if game.current_phase else "unknown",
            "players": [player_snapshot(game.players[i], names[i]) for i in range(2)],
            "event": event_text,
            "events": all_events,
            "game_over": game.game_over,
            "winner": game.winner,
        }
        self.snapshots.append(s)

    # ── Main game loop ──

    def run(self, d1_name, d1_list, d2_name, d2_list):
        self.snapshots = []
        self._log_cursor = 0

        deck1 = self.build_deck(d1_list)
        deck2 = self.build_deck(d2_list)

        game = GameState(rng=self.rng)
        game.setup_game(deck1, deck2)
        game.players[0].deck_name = d1_name
        game.players[1].deck_name = d2_name

        ai1 = AIPlayer(0, d1_name, self.rng)
        ai2 = AIPlayer(1, d2_name, self.rng)
        ais = [ai1, ai2]
        names = [d1_name, d2_name]
        first = game.active_player

        # ── Mulligan ──
        mulligan_events = []
        for p_idx in range(2):
            hand_size = 7
            mull_count = 0
            while hand_size >= 5:
                keep = ais[p_idx].decide_mulligan(game.players[p_idx].hand, hand_size)
                if keep:
                    hand_names = [c.name for c in game.players[p_idx].hand]
                    mulligan_events.append(_evt(
                        "game", "mulligan_keep", p_idx, None,
                        f"P{p_idx+1} keeps {hand_size} cards" + (f" (mulliganed {mull_count})" if mull_count > 0 else ""),
                        "", {"hand_size": hand_size, "mull_count": mull_count, "hand": hand_names}
                    ))
                    if mull_count > 0:
                        to_bottom = ais[p_idx].choose_cards_to_bottom(
                            game.players[p_idx].hand, mull_count)
                        bottom_names = [c.name for c in to_bottom]
                        mulligan_events.append(_evt(
                            "game", "bottom_cards", p_idx, None,
                            f"P{p_idx+1} puts {mull_count} card(s) on bottom",
                            "", {"cards": bottom_names}
                        ))
                        for card in to_bottom:
                            game.players[p_idx].hand.remove(card)
                            card.zone = "library"
                            game.players[p_idx].library.append(card)
                    break
                else:
                    mull_count += 1
                    mulligan_events.append(_evt(
                        "game", "mulligan", p_idx, None,
                        f"P{p_idx+1} mulligans to {7 - mull_count}",
                        "", {"new_hand_size": 7 - mull_count}
                    ))
                    for card in game.players[p_idx].hand[:]:
                        game.players[p_idx].hand.remove(card)
                        card.zone = "library"
                        game.players[p_idx].library.append(card)
                    self.rng.shuffle(game.players[p_idx].library)
                    game.draw_cards(p_idx, 7)
                    hand_size -= 1

        self._log_cursor = len(game.log)  # skip setup log entries
        self.snapshot(game, names,
                      f"Game Start \u2014 {names[first]} plays first",
                      extra_events=mulligan_events)

        # ── Turn loop ──
        while not game.game_over and game.turn_number < game.max_turns:
            active = game.active_player
            ai = ais[active]
            opp_idx = 1 - active
            opp_ai = ais[opp_idx]

            # Untap
            game.current_phase = Phase.UNTAP
            game.untap_step(active)
            ai._pw_activated_this_turn.clear()

            # Upkeep
            game.current_phase = Phase.UPKEEP
            game.process_triggers()
            while not game.stack.is_empty:
                game.resolve_stack()
                game.check_state_based_actions()
                if game.game_over:
                    break
            if game.game_over:
                break

            # Draw
            game.current_phase = Phase.DRAW
            drawn_name = None
            if not (game.turn_number <= 1 and active == first):
                drawn = game.draw_cards(active, 1)
                if drawn:
                    drawn_name = drawn[0].name
                if game.game_over:
                    break

            draw_event = _evt("game", "draw_step", active, drawn_name,
                              f"Drew {drawn_name}" if drawn_name else "Skipped draw (first turn)",
                              "", {"card": drawn_name})
            self.snapshot(game, names,
                          f"Turn {game.turn_number} \u2014 {names[active]}" +
                          (f" draws {drawn_name}" if drawn_name else " (skips draw)"),
                          extra_events=[draw_event])

            # Main Phase 1
            game.current_phase = Phase.MAIN1
            mp1_actions = self._run_main(game, ai, opp_ai, active, names)
            if game.game_over:
                break

            # Planeswalkers
            self._activate_pws(game, ai, active, names)
            if game.game_over:
                break

            # Griselbrand
            self._activate_griselbrand(game, active)
            if game.game_over:
                break

            if mp1_actions:
                self.snapshot(game, names, f"Main Phase 1: {'; '.join(mp1_actions)}")

            # Combat
            game.current_phase = Phase.BEGIN_COMBAT
            game.current_phase = Phase.DECLARE_ATTACKERS
            attackers = ai.decide_attackers(game)

            combat_text = ""
            if attackers:
                atk_names = [f"{a.name} ({a.power}/{a.toughness})" for a in attackers]
                combat_text = f"Attack: {', '.join(atk_names)}"

                for a in attackers:
                    a.attacking = True
                    if Keyword.VIGILANCE not in a.keywords:
                        a.tap()
                    game.trigger_attack(a, active)

                game.process_triggers()
                while not game.stack.is_empty:
                    game.resolve_stack()
                    game.check_state_based_actions()
                    if game.game_over:
                        break
                if game.game_over:
                    break

                game.current_phase = Phase.DECLARE_BLOCKERS
                blocks = opp_ai.decide_blockers(game, attackers)

                block_strs = []
                for att_id, blocker_ids in blocks.items():
                    if blocker_ids:
                        att = game.get_card_by_id(att_id)
                        for bid in blocker_ids:
                            blk = game.get_card_by_id(bid)
                            if att and blk:
                                block_strs.append(f"{blk.name} blocks {att.name}")
                if block_strs:
                    combat_text += f" | Blocks: {'; '.join(block_strs)}"
                else:
                    combat_text += " | No blocks"

                game.current_phase = Phase.COMBAT_DAMAGE
                pre_life = [game.players[0].life, game.players[1].life]
                game.combat_damage(attackers, blocks)
                dmg = pre_life[opp_idx] - game.players[opp_idx].life
                gained = game.players[active].life - pre_life[active]

                if dmg > 0:
                    combat_text += f" | {dmg} damage to P{opp_idx+1}"
                if gained > 0:
                    combat_text += f" | P{active+1} gains {gained} life"

                dead = []
                for p in game.players:
                    for c in p.creatures:
                        if c.is_dead:
                            dead.append(c.name)
                if dead:
                    combat_text += f" | Deaths: {', '.join(dead)}"

                game.check_state_based_actions()
                if game.game_over:
                    self.snapshot(game, names, combat_text)
                    break

                for a in attackers:
                    a.reset_combat()
                for p in game.players:
                    for c in p.creatures:
                        c.reset_combat()
            else:
                combat_text = "No attack"

            self.snapshot(game, names, combat_text)

            game.current_phase = Phase.END_COMBAT

            # Main Phase 2
            game.current_phase = Phase.MAIN2
            mp2_actions = self._run_main(game, ai, opp_ai, active, names)
            if game.game_over:
                break

            # Planeswalkers in MP2
            self._activate_pws(game, ai, active, names)
            if game.game_over:
                break

            if mp2_actions:
                self.snapshot(game, names, f"Main Phase 2: {'; '.join(mp2_actions)}")

            # End/Cleanup
            game.current_phase = Phase.END_STEP
            game.end_of_turn_cleanup()
            game.process_triggers()
            while not game.stack.is_empty:
                game.resolve_stack()
                game.check_state_based_actions()
                if game.game_over:
                    break
            if game.game_over:
                break

            game.current_phase = Phase.CLEANUP
            game.cleanup_step()
            game.switch_active_player()

        # Final snapshot
        if game.game_over and game.winner is not None:
            self.snapshot(game, names, f"GAME OVER \u2014 {names[game.winner]} wins!")
        else:
            self.snapshot(game, names, "GAME OVER \u2014 Timeout")

        return {
            "deck1": d1_name,
            "deck2": d2_name,
            "winner": game.winner,
            "winner_name": names[game.winner] if game.winner is not None else "Draw",
            "total_turns": game.turn_number,
            "snapshots": self.snapshots,
        }

    def _run_main(self, game, ai, opp_ai, active, names):
        actions_taken = []
        max_actions = 50
        count = 0
        failed_cards: Set[int] = set()
        while count < max_actions and not game.game_over:
            decision = ai.decide_main_phase(game, excluded_cards=failed_cards)
            if decision is None:
                break
            action, card, targets = decision

            if action == "play_land":
                game.play_land(ai.player_idx, card)
                tapped = " (tapped)" if card.tapped else ""
                actions_taken.append(f"Play {card.name}{tapped}")

            elif action == "cast_spell":
                cost_str = format_cost(card.template.mana_cost)
                success = game.cast_spell(ai.player_idx, card, targets)
                if success:
                    failed_cards.discard(card.instance_id)
                    dash_label = " [Dash]" if getattr(card, '_dashed', False) else ""
                    if not game.stack.is_empty:
                        top = game.stack.top
                        if top:
                            response = opp_ai.decide_response(game, top)
                            if response:
                                resp_card, resp_targets = response
                                resp_success = game.cast_spell(
                                    opp_ai.player_idx, resp_card, resp_targets)
                                if resp_success:
                                    resp_cost = format_cost(resp_card.template.mana_cost)
                                    actions_taken.append(
                                        f"Cast {card.name} ({cost_str}){dash_label}")
                                    actions_taken.append(
                                        f"[Response] {resp_card.name} ({resp_cost})")
                                    while not game.stack.is_empty:
                                        game.resolve_stack()
                                        game.check_state_based_actions()
                                        if game.game_over:
                                            return actions_taken
                                    count += 1
                                    continue
                    while not game.stack.is_empty:
                        game.resolve_stack()
                        game.check_state_based_actions()
                        if game.game_over:
                            return actions_taken
                    actions_taken.append(f"Cast {card.name} ({cost_str}){dash_label}")
                else:
                    failed_cards.add(card.instance_id)

            count += 1
            game.check_state_based_actions()
            if game.game_over:
                return actions_taken
        return actions_taken

    def _activate_pws(self, game, ai, active, names):
        from engine.game_runner import GameRunner
        player = game.players[active]
        for pw in player.planeswalkers:
            if pw.entered_battlefield_this_turn and pw.loyalty_counters <= 0:
                continue
            if pw.instance_id in ai._pw_activated_this_turn:
                continue
            pw_name = pw.template.name
            if pw_name not in PLANESWALKER_ABILITIES:
                continue
            pw_data = PLANESWALKER_ABILITIES[pw_name]
            opp = game.players[1 - active]
            ability_type = GameRunner._choose_pw_ability(
                None, pw, pw_name, pw_data, player, opp, game)
            game.activate_planeswalker(active, pw, ability_type)
            ai._pw_activated_this_turn.add(pw.instance_id)
            game.check_state_based_actions()
            if game.game_over:
                return

    def _activate_griselbrand(self, game, active):
        player = game.players[active]
        for creature in player.creatures:
            if creature.name == "Griselbrand":
                while player.life >= 14 and len(player.hand) < 14:
                    game.activate_griselbrand(active, creature)
                    if game.game_over:
                        return
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deck1", default="4c Omnath")
    parser.add_argument("--deck2", default="Affinity")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", default="output/replay_data.json")
    args = parser.parse_args()

    for d in [args.deck1, args.deck2]:
        if d not in MODERN_DECKS:
            print(f"Unknown deck: {d}. Available: {', '.join(MODERN_DECKS.keys())}")
            return

    db = CardDatabase("ModernAtomic.json")
    rng = random.Random(args.seed)
    gen = ReplayGenerator(db, rng)

    data = gen.run(
        args.deck1, MODERN_DECKS[args.deck1]["mainboard"],
        args.deck2, MODERN_DECKS[args.deck2]["mainboard"],
    )

    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Replay data written to {args.output}")
    print(f"  {len(data['snapshots'])} snapshots, {data['total_turns']} turns")


if __name__ == "__main__":
    main()
