"""
Permanent effects — extracted from engine/game_state.py (Commit 5b).

Lifecycle helpers for permanents + simple resource changes:
- reanimate: graveyard → battlefield under controller's control,
  with optional exile-at-EOT and haste (Goryo's, Persist).
- create_token: spawn tokens from TOKEN_DEFS or construct them from
  token_type/P/T/keywords with Affinity-aware auto-scaling.
- _creature_dies: death trigger fan-out + undying/persist/dredge
  checks + LTB-to-graveyard zone move.
- _permanent_destroyed: non-creature destruction path.
- _exile_permanent: move a permanent to exile.
- _bounce_permanent: move a permanent to owner's hand.
- gain_life: life gain with lifegain-trigger fan-out.
- produce_energy: add energy counters.
- spend_energy_for_effect: spend energy counters.

Methods are static and take game: GameState as the first argument,
matching the manager pattern used across engine/*.py.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, List, Set

from .cards import (COUNTER_KIND_MINUS,
    CardInstance, CardTemplate, CardType, Keyword, Supertype, Color,
)
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .mana import ManaCost
from .player_state import TOKEN_DEFS

if TYPE_CHECKING:
    from .game_state import GameState


# ─── PRINTED TOKENS (CR 111.3, 111.4) ────────────────────────────────

# The card types, supertypes and colours a token's creator may print that
# the token owner builds; any other refuses the spec (`printed_token`).
_TOKEN_CARD_TYPES = {"artifact": CardType.ARTIFACT,
                     "creature": CardType.CREATURE,
                     "enchantment": CardType.ENCHANTMENT}
_TOKEN_SUPERTYPES = {s.value: s for s in Supertype}
_TOKEN_COLORS = {"W": Color.WHITE, "U": Color.BLUE, "B": Color.BLACK,
                 "R": Color.RED, "G": Color.GREEN}
_TOKEN_KEYWORDS = {k.value: k for k in Keyword}


def _literal(amount):
    """The printed integer of a LITERAL amount; None otherwise."""
    from .effect_spec import AmountKind
    if amount is None or amount.kind is not AmountKind.LITERAL \
            or amount.quantity is not None or amount.ref is not None \
            or amount.inner is not None:
        return None
    return amount.n


def _granted_mana_units(granted) -> "List[List[str]] | None":
    """The mana units of a token's granted abilities when every one is a
    "Sacrifice this <permanent>: Add <mana>" mana ability -- the derivation
    that types a card's own (`mana_units.sacrifice_units`); [] for none;
    None for any other ability, which the token owner does not build."""
    from .effect_spec import HostKind
    from .mana_units import sacrifice_units
    if not granted:
        return []
    for h in granted:
        cost = dict(h.cost.items) if h.cost is not None else {}
        if h.kind is not HostKind.MANA_ABILITY or not cost.get("sacrifice_self"):
            return None
    return sacrifice_units(granted) or None


def printed_token(spec) -> "dict | None":
    """The characteristics a token's creator defines (CR 111.3), as the
    token owner builds them from the typed `TokenSpec`: its card types,
    supertypes, subtypes, colours, printed power and toughness, keywords
    and granted "Sacrifice this token: Add <mana>" abilities (as
    `sacrifice_mana_units`). An unnamed token is named for its subtypes
    plus "Token" (CR 111.4). None for a predefined token, a copy, a named
    token, a power or toughness that is not printed, a keyword with a
    parameter or one the engine does not type, or any other granted
    ability: the owner builds none of those from the spec."""
    from .effect_spec import TokenSpec
    if not isinstance(spec, TokenSpec) or spec.predefined is not None \
            or spec.copy_of is not None or spec.copy_except \
            or spec.name is not None or not spec.types \
            or any(t not in _TOKEN_CARD_TYPES for t in spec.types) \
            or any(s not in _TOKEN_SUPERTYPES for s in spec.supertypes) \
            or any(c not in _TOKEN_COLORS for c in spec.colors):
        return None
    creature = "creature" in spec.types
    power, toughness = _literal(spec.power), _literal(spec.toughness)
    if creature and (power is None or toughness is None):
        return None
    if not creature and (spec.power is not None or spec.toughness is not None):
        return None
    keywords = set()
    for name, param in spec.keywords:
        if param is not None or name not in _TOKEN_KEYWORDS:
            return None
        keywords.add(_TOKEN_KEYWORDS[name])
    units = _granted_mana_units(spec.granted)
    if units is None:
        return None
    subtypes = [w.title() for w in spec.subtypes]
    # The token's text is its granted abilities (CR 111.3).
    text = " ".join(h.text.replace("~", "this token") for h in spec.granted)
    return {
        "name": " ".join(subtypes or [t.title() for t in spec.types]) + " Token",
        "card_types": [_TOKEN_CARD_TYPES[t] for t in spec.types],
        "supertypes": [_TOKEN_SUPERTYPES[s] for s in spec.supertypes],
        "subtypes": subtypes,
        "colors": {_TOKEN_COLORS[c] for c in spec.colors},
        "power": power,
        "toughness": toughness,
        "keywords": keywords,
        "sacrifice_mana_units": units,
        "activated_abilities": _token_abilities(text),
        "oracle_text": text,
    }


# ─── PREDEFINED TOKENS (CR 111.10) ───────────────────────────────────

# The predefined kinds the token owner builds. Each is a colorless artifact
# token with its kind as its subtype and the ability its CR 111.10 rule
# prints (the kind's row in `keywords.EXPANSIONS`). Any other kind is
# refused by the executors that create tokens.
PREDEFINED_TOKEN_KINDS = frozenset({"treasure", "food", "clue"})


@lru_cache(maxsize=None)
def _token_abilities(text: str) -> tuple:
    """The activated abilities a token's text gives it (its predefined
    rule's, or the ones its creator grants it), typed once per text through
    the parse-once owner every card's text goes through."""
    from .oracle_parser import parse_activated_abilities
    return tuple(parse_activated_abilities(text) or ())


@lru_cache(maxsize=None)
def _predefined_parts(kind: str):
    """The text CR 111.10 gives a token of `kind` -- the token names itself
    "this token" -- and its abilities, typed once per kind through the
    parse-once owners every card's text goes through."""
    from .effect_grammar.keywords import expansion_text
    from .oracle_parser import parse_sacrifice_mana_units
    text = expansion_text(kind).replace("~", "this token")
    units = parse_sacrifice_mana_units(text) or []
    return (text, _token_abilities(text), tuple(tuple(u) for u in units))


def predefined_token(kind: str) -> "dict | None":
    """The characteristics CR 111.10 defines for a predefined token of
    `kind`, as `_put_tokens` builds them: "<Kind> Token", a colorless
    artifact with subtype <Kind>, the text its rule prints and the
    abilities typed from it (a sacrifice-for-mana ability also as
    `sacrifice_mana_units`, the field mana payment spends). None for a
    kind the owner does not build."""
    if kind not in PREDEFINED_TOKEN_KINDS:
        return None
    text, abilities, units = _predefined_parts(kind)
    return {
        "name": f"{kind.title()} Token",
        "card_types": [CardType.ARTIFACT],
        "supertypes": [],
        "subtypes": [kind.title()],
        "colors": set(),
        "power": None,
        "toughness": None,
        "keywords": set(),
        "sacrifice_mana_units": units,
        "activated_abilities": abilities,
        "oracle_text": text,
    }


def _audit_predefined_tokens(game, kind: str, creator: int,
                             tokens) -> None:
    """CR 111.10 / 111.2, restated from each token's raw template and the
    rule's own row (never from the builder): a <Kind> token is a colorless
    artifact with subtype <Kind> whose text is the row, it has a typed
    ability exactly when the row prints one, a sacrifice-for-mana ability
    is in the field payment spends, and its creator owns and controls it.
    Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    from .effect_grammar.keywords import EXPANSIONS
    row = EXPANSIONS[kind].template
    problems = []
    for t in tokens:
        tt = t.template
        abilities = tt.activated_abilities or []
        if tt.card_types != [CardType.ARTIFACT] or tt.colors \
                or [s.lower() for s in tt.subtypes] != [kind]:
            problems.append(f"{t.name} is {tt.card_types} {tt.subtypes} "
                            f"{tt.colors}")
        if (tt.oracle_text or "").replace("this token", "~") != row \
                or bool(abilities) != bool(row):
            problems.append(f"{t.name} has {len(abilities)} abilities for "
                            f"{row!r}")
        if any(a.is_mana_ability and a.cost.sacrifice_self
               for a in abilities) != bool(tt.sacrifice_mana_units):
            problems.append(f"{t.name}'s sacrifice mana is not spendable")
        if t.owner != creator or t.controller != creator:
            problems.append(f"{t.name} is P{t.controller + 1}'s, created by "
                            f"P{creator + 1}")
    rules_audit.check("111.10/predefined_token", not problems,
                      "; ".join(problems), game=game)


_INVESTIGATES = re.compile(r"\binvestigates?\b")
# The printed forms of a token created tapped (CR 110.5b): "creates two
# tapped ... tokens", "a token ... that's tapped (and attacking)", "a token
# that's a copy of ~ that's tapped". "for each tapped creature" is a count.
_ENTERS_TAPPED = re.compile(
    r"\bcreates?\b[^.;]*?\btapped\b[^.;]*?\btokens?\b"
    r"|\btokens?\b(?:[^.;]*? that(?:'s| are))? tapped\b"
    r"|\bcop(?:y|ies) of\b[^.;]*? (?:that(?:'s| are) )?tapped\b", re.I)


def audit_token_instruction(game, raw: str, made: dict,
                            source_name: str) -> None:
    """Rules audit of the tokens one instruction made (`made`: creating
    player -> tokens), restated from the instruction's printed text `raw`
    -- never from its typed spec. CR 110.5b: a token its creator puts onto
    the battlefield tapped enters tapped (one it does not may still enter
    tapped by another effect). CR 701.16a: an instruction printed as
    investigating creates only Clues. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    if _ENTERS_TAPPED.search(raw or ""):
        rules_audit.check(
            "110.5b/token_entry_status",
            all(t.tapped for tokens in made.values() for t in tokens),
            f"{source_name}: a token stated tapped entered untapped",
            game=game)
    if _INVESTIGATES.search(raw or ""):
        kinds = {tuple(t.template.subtypes)
                 for tokens in made.values() for t in tokens}
        rules_audit.check("701.16a/investigate_creates_a_clue",
                          kinds <= {("Clue",)},
                          f"{source_name} investigated and made "
                          f"{sorted(kinds)}", game=game)


def observer_matches(spec: dict, creature: CardInstance) -> bool:
    """Does a typed dies observer's filter admit the dying creature? Only
    the subtype filter of "one or more other <subtype>s you control die"
    narrows it (CR 205.3, read from the creature's own subtypes); the scope
    (whose creature) is checked by the caller."""
    wanted = spec.get("subtypes")
    if not wanted:
        return True
    return any(st.lower() in wanted for st in creature.effective_subtypes)


def _prints_observer(perm: CardInstance, spec: dict) -> bool:
    """A self-transforming observer is printed on the face that transforms
    (CR 712.8): a permanent showing its back face has no such trigger."""
    return not (spec["kind"] == "transform_self"
                and getattr(perm, "is_transformed", False))


def _audit_dies_observers(game: "GameState", creature: CardInstance,
                          controller: int, fired: set) -> None:
    """CR 603.2, restated from the raw fields (not from `observer_matches`):
    a creature's death triggers every dies observer on the battlefield whose
    scope covers its controller and whose subtype (if any) is one of the
    creature's own -- whatever the creature's text. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    dying = {st.lower() for st in creature.effective_subtypes}
    for p_idx, player in enumerate(game.players):
        for perm in player.battlefield:
            spec = perm.template.creature_dies_observer
            if not spec or not _prints_observer(perm, spec):
                continue
            scope = spec["scope"]
            if (scope == "you" and controller != p_idx) or \
                    (scope == "opponent" and controller == p_idx):
                continue
            wanted = spec.get("subtypes")
            if wanted and not dying & set(wanted):
                continue
            rules_audit.check(
                "603.2/dies_observer", id(perm) in fired,
                f"{creature.name} died and {perm.name}'s observer "
                f"({spec['kind']}) did not trigger", game=game)


class PermanentEffects:
    """Stateless lifecycle + resource helpers for permanents."""

    @staticmethod
    def reanimate(game: "GameState", controller: int, target_card: CardInstance,
                  exile_at_eot: bool = False, give_haste: bool = False):
        """Put a creature from graveyard onto the battlefield."""
        player = game.players[controller]
        if target_card not in player.graveyard:
            return

        player.graveyard.remove(target_card)
        target_card.controller = controller
        target_card.enter_battlefield()
        if give_haste:
            target_card.temp_keywords.add(Keyword.HASTE)
        player.battlefield.append(target_card)

        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Reanimate {target_card.name}")

        if exile_at_eot:
            game.register_end_of_turn_exile(target_card, controller)

        # Trigger ETB
        game._handle_permanent_etb(target_card, controller)

    # ─── LAND ANIMATION (Track H) ────────────────────────────────

    @staticmethod
    def animate_land(game: "GameState", controller: int,
                     land: CardInstance) -> bool:
        """Execute an activated land-animation line ("{cost}: … this
        land becomes an N/M … creature … until end of turn").

        Rules enforcement only — no scoring.  The cost is paid by
        tapping OTHER untapped lands (generic-count payment, the same
        model the granted-ability dispatch uses); the source stays
        untapped so it can attack.  Returns True when the activation
        was legal and executed.
        """
        from .oracle_parser import parse_land_animation
        player = game.players[controller]
        if land not in player.battlefield or not land.template.is_land:
            return False
        if land.is_animated:
            return False  # animating twice this turn adds nothing here
        spec = parse_land_animation(land.template.oracle_text or '')
        if spec is None:
            return False
        payers = [l for l in player.untapped_lands if l is not land]
        if len(payers) < spec['cost']:
            return False
        for payer in payers[:spec['cost']]:
            payer.tapped = True
        land.is_animated = True
        land.temp_power_mod += spec['power']
        land.temp_toughness_mod += spec['toughness']
        kw_by_value = {k.value: k for k in Keyword}
        for kw in spec['keywords']:
            kw_enum = kw_by_value.get(kw.replace(' ', '_'))
            if kw_enum is not None:
                land.temp_keywords.add(kw_enum)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {land.name} becomes "
            f"a {spec['power']}/{spec['toughness']} creature until end "
            f"of turn (pays {spec['cost']} mana)")
        return True

    # ─── AURA ATTACHMENT (CR 303.4) ──────────────────────────────

    @staticmethod
    def _aura_host_is_legal(host, restriction: str) -> bool:
        """Does `host` satisfy an Aura's "Enchant <quality>" (CR 303.4a)?

        Handles the two shapes that matter for attachment: a card TYPE
        ('land', 'creature', 'artifact', 'enchantment', 'planeswalker') and a
        SUBTYPE ('forest', 'goblin', …). Qualities carrying an additional
        controller clause ('creature you control') are matched on their type
        head; the controller check is applied by the caller, which only ever
        offers its own permanents as candidates.
        """
        from .cards import CardType
        head = (restriction or '').split(' you ')[0].strip()
        type_map = {
            'land': CardType.LAND, 'creature': CardType.CREATURE,
            'artifact': CardType.ARTIFACT, 'enchantment': CardType.ENCHANTMENT,
            'planeswalker': CardType.PLANESWALKER,
        }
        if head in type_map:
            return type_map[head] in host.template.card_types
        # Otherwise treat the quality as a subtype ("Enchant Forest").
        subtypes = {s.lower() for s in getattr(host.template, 'subtypes', [])}
        return head in subtypes

    @staticmethod
    def attach_aura(game: "GameState", aura: "CardInstance", controller: int):
        """Attach an Aura to a legal permanent its controller controls.

        CR 303.4a: an Aura enchants an object chosen by its "Enchant
        <quality>" ability. Returns the host it attached to, or ``None`` when
        no legal host exists (CR 303.4c — the Aura then does nothing; the SBA
        that bins it is a separate concern).

        Attachment reuses the Equipment convention already in this engine —
        an `attached_{instance_id}` tag on the HOST plus a back-reference on
        the Aura — so both attachment kinds are discoverable the same way.
        Among equally legal hosts an untapped one is preferred, since the
        common case (mana Auras) is only useful on a land that can still tap.
        """
        restriction = getattr(aura.template, 'aura_enchant_restriction', None)
        if not restriction:
            return None
        candidates = [
            c for c in game.players[controller].battlefield
            if c.instance_id != aura.instance_id
            and PermanentEffects._aura_host_is_legal(c, restriction)
        ]
        if not candidates:
            return None
        host = min(candidates, key=lambda c: (1 if c.tapped else 0))
        host.instance_tags.add(f"attached_{aura.instance_id}")
        aura.attached_to_id = host.instance_id
        PermanentEffects._choose_entry_mana_color(game, aura, controller)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {aura.name} enchants "
            f"{host.name}")
        return host

    @staticmethod
    def _choose_entry_mana_color(game: "GameState", perm, controller: int):
        """Resolve an "As this enters, choose a color" entry choice.

        The engine enumerates the legal colours (the option set the parser
        recorded in `aura_mana_units`) and hands the DECISION to the callback
        seam — engine layer enforces the rule, AI layer picks. An out-of-range
        answer, or callbacks that predate this seam, fall back to
        `DefaultCallbacks.choose_mana_color`.
        """
        if not getattr(perm.template, 'aura_mana_color_chosen', False):
            return
        options = sorted({c for unit in perm.template.aura_mana_units
                          for c in unit})
        if not options:
            return
        chooser = getattr(getattr(game, 'callbacks', None),
                          'choose_mana_color', None)
        picked = None
        if chooser is not None:
            picked = chooser(game, controller, perm, options)
        if picked not in options:
            from .callbacks import DefaultCallbacks
            picked = DefaultCallbacks.choose_mana_color(
                DefaultCallbacks(), game, controller, perm, options)
        perm.chosen_color = picked if picked in options else options[0]
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {perm.name} enters — "
            f"colour chosen: {perm.chosen_color}")

    @staticmethod
    def aura_granted_mana_units(game: "GameState", host) -> list:
        """Mana units granted to `host` by Auras attached to it.

        Read by `ManaPayment.land_mana_units`, the single per-land unit
        resolver, so every consumer of a land's mana (payment, capacity
        estimate) sees the Aura's contribution without its own special case.
        """
        out = []
        controller = getattr(host, 'controller', None)
        if controller is None:
            return out
        for perm in game.players[controller].battlefield:
            units = getattr(perm.template, 'aura_mana_units', None)
            if not units:
                continue
            if getattr(perm, 'attached_to_id', None) != host.instance_id:
                continue
            # "of the chosen color" (CR 614-style entry choice): the option set
            # was recorded at parse time, but the Aura picked ONE colour as it
            # entered. Narrow to that colour — modelling it as any-colour would
            # hand the deck colour fixing the card does not provide.
            chosen = getattr(perm, 'chosen_color', None)
            if getattr(perm.template, 'aura_mana_color_chosen', False) and chosen:
                out.extend([[chosen] for _ in units])
                continue
            out.extend([list(u) for u in units])
        return out

    @staticmethod
    def attached_host(game: "GameState", perm) -> "CardInstance | None":
        """The permanent `perm` is attached to (CR 301.5, 303.4), or None.
        An Equipment's bearer carries the `equipped_<id>` tag
        (`GameState.attach_equipment`, the one owner of attaching it); an
        Aura records `attached_to_id`. Read-only."""
        tag = f"equipped_{perm.instance_id}"
        aura_host = getattr(perm, "attached_to_id", None)
        for player in game.players:
            for c in player.battlefield:
                if tag in getattr(c, "instance_tags", ()):
                    return c
        if aura_host is None:
            return None
        return next((c for player in game.players for c in player.battlefield
                     if c.instance_id == aura_host), None)

    # ─── TAP-FOR-MANA TRIGGERS (CR 605.1b) ───────────────────────

    # Basic land subtypes a trigger may name ("whenever you tap a Forest for
    # mana"). Kept here rather than re-derived per call; the same five types
    # domain counting uses.
    _WATCH_LAND_SUBTYPES = {'plains', 'island', 'swamp', 'mountain', 'forest'}

    @staticmethod
    def tap_trigger_matches(watch: str, source) -> bool:
        """Does `source` satisfy a `TapForManaTrigger`'s watch filter?

        `watch` is the noun phrase the parser lifted out of the oracle line, so
        this is a dispatch over parsed card QUALITIES — types, land subtypes,
        tokenness — not over card identities. A trigger naming a quality this
        engine cannot express matches nothing rather than matching everything.
        """
        from .cards import CardType
        tmpl = source.template
        if watch in PermanentEffects._WATCH_LAND_SUBTYPES:
            return bool(tmpl.is_land) and watch in {
                s.lower() for s in getattr(tmpl, 'subtypes', ())}
        if watch == 'land':
            return bool(tmpl.is_land)
        if watch == 'nonland permanent':
            return not tmpl.is_land
        if watch == 'creature':
            return CardType.CREATURE in tmpl.card_types
        if watch == 'artifact':
            return CardType.ARTIFACT in tmpl.card_types
        if watch == 'artifact token':
            return (CardType.ARTIFACT in tmpl.card_types
                    and bool(getattr(source, 'is_token', False)))
        return False

    @staticmethod
    def tap_trigger_bonus_units(game: "GameState", source, base_units) -> list:
        """Extra mana units granted by tap-for-mana triggers watching `source`.

        CR 605.1b: a triggered ability that triggers off a mana ability and
        itself adds mana is a mana ability, so the extra mana is in the pool in
        time to pay the same cost. Modelling it as extra UNITS on the tapped
        source is what makes that true here: the units come from
        `ManaPayment.land_mana_units`, the one resolver both the payment path
        and every capacity estimate read, so capacity cannot drift from
        production.

        `base_units` is what the source already produces, needed for the
        "add one mana of any type that permanent produced" rider.

        Triggers are controller-scoped ("whenever YOU tap"), so only the
        source's own controller's battlefield is scanned. Multiple copies each
        trigger, so the units accumulate.
        """
        controller = getattr(source, 'controller', None)
        if controller is None or not base_units:
            return []
        out = []
        for perm in game.players[controller].battlefield:
            trig = getattr(perm.template, 'tap_for_mana_trigger', None)
            if trig is None:
                continue
            if not PermanentEffects.tap_trigger_matches(trig.watch, source):
                continue
            if trig.mirror_source:
                # One extra mana of a type the source produced — the union of
                # its own options, since which colour it "produced" is only
                # settled at payment time in this unit model.
                mirrored = sorted({c for unit in base_units for c in unit})
                if mirrored:
                    out.append(mirrored)
                continue
            out.extend([list(u) for u in trig.units])
        return out

    # ─── TOKEN GENERATION ────────────────────────────────────────


    @staticmethod
    def create_token(game: "GameState", controller: int, token_type: str,
                     count: int = 1, power: int = None, toughness: int = None,
                     extra_keywords: Set[Keyword] = None,
                     source_oracle: str = None,
                     subtypes: List[str] = None,
                     colors: Set[Color] = None,
                     token_spec=None,
                     tapped: bool = False) -> List[CardInstance]:
        """Create token creatures on the battlefield.

        A token its creator puts onto the battlefield tapped (`tapped`, or
        the typed spec's) enters tapped (CR 110.5b).

        With `token_spec`, a typed `TokenSpec` its creator prints, the
        token has exactly the characteristics `printed_token` builds from it
        (CR 111.3, 111.4) -- no text is read; the other arguments are
        unused.
        The player who creates a token is its owner and it enters under
        their control (CR 111.2).

        Generic-first design (post-Phase-1C-followup): when
        ``source_oracle`` is provided, the spawning card's oracle
        text is parsed via ``engine.oracle_parser.parse_token_spec``
        to extract P/T, types, and keywords directly. This avoids
        hardcoding card-name-specific token registrations in
        TOKEN_DEFS.

        A predefined kind (Treasure, Food, Clue) is the object its CR
        111.10 rule defines (`predefined_token`), whatever else is passed.

        TOKEN_DEFS is preserved as a fallback for the small set of
        canonical creature tokens (Goblin, Soldier, ...) where oracle
        text is not always passed by the caller. Future cleanup will
        migrate every caller to pass source_oracle so TOKEN_DEFS can be
        retired.
        """
        if token_spec is not None:
            built = printed_token(token_spec)
            if built is None:
                raise ValueError(f"the token owner builds no {token_spec!r}")
            return PermanentEffects._put_tokens(
                game, controller, count, built,
                tapped=tapped or token_spec.tapped)
        # A predefined token is the object its CR 111.10 rule defines; the
        # other arguments describe nothing it has.
        built = predefined_token(token_type)
        if built is not None:
            tokens = PermanentEffects._put_tokens(game, controller, count,
                                                  built, tapped=tapped)
            _audit_predefined_tokens(game, token_type, controller, tokens)
            return tokens
        tokens = []
        # Generic path: parse from the spawning card's oracle text.
        spec = None
        if source_oracle:
            from .oracle_parser import parse_token_spec
            spec = parse_token_spec(source_oracle)

        if spec is not None:
            from .cards import Keyword as _Kw
            kw_lookup = {k.value: k for k in _Kw}
            type_lookup = {
                "artifact": CardType.ARTIFACT,
                "creature": CardType.CREATURE,
                "enchantment": CardType.ENCHANTMENT,
            }
            t_name = spec["subtype"]
            t_types = [type_lookup[t] for t in spec["types"]
                       if t in type_lookup]
            # A named noncreature token (Munitions et al.) stays a
            # noncreature permanent — do NOT force CREATURE onto it, or
            # it becomes an illegal attacker. Only the P/T creature-token
            # shape defaults to creature.
            if not spec.get("is_noncreature") and CardType.CREATURE not in t_types:
                t_types.append(CardType.CREATURE)
            t_power = spec["power"]
            t_toughness = spec["toughness"]
            kw_set = {kw_lookup[w.replace(" ", "_")]
                      for w in spec["keywords"]
                      if w.replace(" ", "_") in kw_lookup}
            from .cards import Color as _Color
            _letter_color = {"W": _Color.WHITE, "U": _Color.BLUE,
                             "B": _Color.BLACK, "R": _Color.RED,
                             "G": _Color.GREEN}
            t_colors = {_letter_color[c] for c in spec.get("colors", [])
                        if c in _letter_color}
        else:
            token_def = TOKEN_DEFS.get(token_type)
            if not token_def:
                token_def = (token_type.title(), [CardType.CREATURE],
                             power or 1, toughness or 1, set())
            t_name, t_types, t_power, t_toughness, t_keywords = token_def
            kw_set = set(t_keywords)
            t_colors = set()  # TOKEN_DEFS resource tokens carry no colour spec

        if power is not None:
            t_power = power
        if toughness is not None:
            t_toughness = toughness
        if extra_keywords:
            kw_set |= extra_keywords
        if colors:
            t_colors = set(colors)

        # Oracle text on the generated template — when source_oracle
        # carries a "with 'gets +N/+N for each artifact ...'" clause,
        # _dynamic_base_power picks up the scaling regex. We extract
        # the inner-quoted text from source_oracle when present.
        token_oracle = ""
        if source_oracle:
            import re as _re
            inner = _re.search(
                r"token(?:\s+named\s+\w+)?\s+with\s+['\"]([^'\"]+)['\"]",
                source_oracle, flags=_re.IGNORECASE,
            )
            if inner:
                token_oracle = inner.group(1)
        if not token_oracle and token_type == "construct":
            # Legacy fallback for callers that don't pass oracle.
            token_oracle = (
                "This creature gets +1/+1 for each artifact you control."
            )

        from .oracle_parser import parse_has_artifact_count_scaling as _parse_art_scale
        _art_scale = _parse_art_scale(token_oracle)
        for _ in range(count):
            template = CardTemplate(
                name=f"{t_name} Token",
                card_types=list(t_types),
                mana_cost=ManaCost(),
                power=t_power,
                toughness=t_toughness,
                keywords=kw_set,
                colors=set(t_colors),
                tags={"token", "creature"},
                oracle_text=token_oracle,
                subtypes=list(subtypes or ()),
            )
            template.has_artifact_count_scaling = _art_scale
            # A token created "with '<ability>'" carries that ability. When it
            # is a sacrifice-for-mana ability (Eldrazi Spawn, Treasure), record
            # the one-shot units so the token counts as ramp rather than as a
            # dead 0/1 body.
            from .oracle_parser import (
                parse_sacrifice_mana_units as _parse_sac_mana)
            template.sacrifice_mana_units = (
                _parse_sac_mana(token_oracle) or [])
            template.activated_abilities = list(
                _token_abilities(token_oracle))
            instance = CardInstance(
                template=template,
                owner=controller,
                controller=controller,
                instance_id=game.next_instance_id(),
                zone="battlefield",
            )
            instance.is_token = True  # CR 111 — see cards.py field doc
            instance._game_state = game
            instance.enter_battlefield()
            game.players[controller].battlefield.append(instance)
            # CR 603.6a / 111.10: a token entering is a permanent-entering
            # event like any other — it fires the token's own ETB and every
            # "whenever another creature you control enters" watcher (Guide of
            # Souls, Impact Tremors, aristocrats, ...). enter_battlefield() is
            # pure state; the trigger dispatch is _handle_permanent_etb, which
            # create_token had skipped (the undying/persist re-entry paths in
            # this file already call it). No StackItem — tokens are never cast
            # (CR 111.2), matching the reanimate/blink item=None convention.
            game._handle_permanent_etb(instance, controller)
            tokens.append(instance)

        if count > 0:
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Create {count}x {t_name} token(s)")
        return tokens

    @staticmethod
    def _put_tokens(game: "GameState", controller: int, count: int,
                    built: dict, tapped: bool = False) -> List[CardInstance]:
        """Put `count` tokens with the characteristics `built`
        (`printed_token`, `predefined_token`) onto the battlefield under
        `controller`'s control (CR 111.2), tapped when their creator says
        so (CR 110.5b). Each enters like any permanent: its own and every
        watcher's enter triggers fire (CR 603.6a)."""
        tokens = []
        creature = CardType.CREATURE in built["card_types"]
        for _ in range(count):
            template = CardTemplate(
                name=built["name"],
                card_types=list(built["card_types"]),
                supertypes=list(built["supertypes"]),
                subtypes=list(built["subtypes"]),
                mana_cost=ManaCost(),
                power=built["power"],
                toughness=built["toughness"],
                keywords=set(built["keywords"]),
                colors=set(built["colors"]),
                tags={"token", "creature"} if creature else {"token"},
                oracle_text=built["oracle_text"],
            )
            template.sacrifice_mana_units = [
                list(u) for u in built["sacrifice_mana_units"]]
            template.activated_abilities = list(
                built.get("activated_abilities", ()))
            instance = CardInstance(template=template, owner=controller,
                                    controller=controller,
                                    instance_id=game.next_instance_id(),
                                    zone="battlefield")
            instance.is_token = True
            instance._game_state = game
            instance.enter_battlefield()
            if tapped:
                instance.tapped = True
            game.players[controller].battlefield.append(instance)
            game._handle_permanent_etb(instance, controller)
            tokens.append(instance)
        if count > 0:
            kind = built["name"][:-len(" Token")]
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Create {count}x {kind} token(s)")
        return tokens

    @staticmethod
    def amass(game: "GameState", controller: int, n: int,
              subtype: str) -> CardInstance:
        """CR 701.47a, "Amass [subtype] N": if `controller` controls no
        Army creature, create a 0/0 black [subtype] Army creature token
        (through `create_token`, the token owner: it enters like any
        permanent); then put N +1/+1 counters on an Army creature they
        control (through `add_plus_counters`, the counter funnel). The one
        owner of amass. Its "becomes a [subtype] in addition" clause for an
        Army of another subtype is not modelled. Returns the Army."""
        kind = subtype.title()
        army = next((c for c in game.players[controller].battlefield
                     if c.effective_is_creature
                     and "Army" in (c.effective_subtypes or ())), None)
        if army is None:
            army = PermanentEffects.create_token(
                game, controller, f"{kind} Army", power=0, toughness=0,
                subtypes=[kind, "Army"], colors={Color.BLACK})[0]
        army.add_plus_counters(n, game)
        game.log.append(f"T{game.display_turn} P{controller+1}: amass "
                        f"{kind}s {n} -- {army.name} is "
                        f"{army.power}/{army.toughness}")
        return army

    # ─── PLANESWALKER ABILITIES ──────────────────────────────────


    @staticmethod
    def _creature_dies(game: "GameState", creature: CardInstance,
                       cause: str = ""):
        """Handle a creature dying: put into a graveyard from the
        battlefield by any means (CR 700.4). The zone funnel hands every
        such move here; the move itself goes back through the funnel as
        the death (`dying=True`)."""
        owner = creature.owner
        controller = creature.controller

        # CR 614.6 / 700.4: a static "would die / would be put into a
        # graveyard, exile it instead" (asked of the zone funnel's one
        # matcher) replaces the death: the creature is exiled and did not
        # die, so undying, persist and modular -- dies triggers -- and
        # every death count, trigger and observer below stay silent.
        exiled_instead = game.zone_mgr.graveyard_exile_source(
            game, creature, "battlefield") is not None

        # Replacement effects: counters must be readable BEFORE any cleanup.
        # Undying (CR 702.94): return to battlefield with +1/+1 counter if no +1/+1 counter.
        if (not exiled_instead and Keyword.UNDYING in creature.keywords
                and creature.plus_counters == 0):
            if creature in game.players[controller].battlefield:
                game.players[controller].battlefield.remove(creature)
            creature.zone = "graveyard"  # transitional; CR 701.12 replacement redirects to BTL
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            creature.add_plus_counters(1, game)
            game.players[controller].battlefield.append(creature)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (undying)")
            # CR 603.6a: the returned creature is a NEW object entering the
            # battlefield — its ETB triggers fire again (mirror reanimate()).
            game._handle_permanent_etb(creature, controller)
            return

        # Persist (CR 702.78): return to battlefield with -1/-1 counter if no -1/-1 counter.
        if (not exiled_instead and Keyword.PERSIST in creature.keywords
                and creature.minus_counters == 0):
            if creature in game.players[controller].battlefield:
                game.players[controller].battlefield.remove(creature)
            creature.zone = "graveyard"  # transitional; CR 701.12 replacement redirects to BTL
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            game.players[controller].battlefield.append(creature)
            # CR 702.77a: returns with a -1/-1 counter — through the counter
            # funnel, so a counter-placement replacement (Vizier shape)
            # applies exactly as it does in paper.
            creature.adjust_counters(COUNTER_KIND_MINUS, 1)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (persist)")
            # CR 603.6a: the returned creature is a NEW object entering the
            # battlefield — its ETB triggers fire again (mirror reanimate()).
            game._handle_permanent_etb(creature, controller)
            return

        # Equipment falls off: read instance_tags BEFORE move_card clears them.
        equip_tags_on_creature = [
            t for t in creature.instance_tags
            if t.startswith("equipped_")
        ]
        if equip_tags_on_creature:
            for tag in equip_tags_on_creature:
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    equip_perm = game.get_card_by_id(equip_iid)
                    if equip_perm:
                        equip_perm.instance_tags.discard("equipment_attached")
                        equip_perm.instance_tags.add("equipment_unattached")
                        game.log.append(
                            f"T{game.display_turn}: {equip_perm.template.name} falls off "
                            f"{creature.name} (unattached)")
                except (ValueError, AttributeError):
                    pass

        # Modular (CR 702.43): capture +1/+1 counters BEFORE zone_mgr.move_card
        # clears them via _cleanup_leaving_battlefield.  The triggered ability
        # fires after the creature is in the graveyard, but the counter count
        # must be read while the creature is still on the battlefield.
        # Keyed on Keyword.MODULAR — no card name involved.
        _modular_counters = 0
        if Keyword.MODULAR in creature.keywords and creature.plus_counters > 0:
            _modular_counters = creature.plus_counters

        # Route zone mutation through the funnel: single owner of battlefield→graveyard
        # list mutation, zone attribute, and leaving-battlefield cleanup.
        game.zone_mgr.move_card(game, creature, "battlefield", "graveyard",
                                cause=cause, dying=True)
        if exiled_instead:
            return                       # it did not die (CR 700.4)
        game.players[controller].creatures_died_this_turn += 1

        # Modular death trigger: transfer captured counters to best artifact creature.
        # The AI always takes the optional transfer when a valid target exists
        # (unconditionally beneficial). Runs after zone move so the dead creature
        # is no longer a valid target for itself.
        if _modular_counters > 0:
            _artifact_creatures = [
                c for c in game.players[controller].battlefield
                if CardType.CREATURE in c.effective_card_types
                and CardType.ARTIFACT in c.effective_card_types
            ]
            if _artifact_creatures:
                _target = max(_artifact_creatures, key=lambda c: c.power)
                _target.add_plus_counters(_modular_counters, game, source=creature)
                game.log.append(
                    f"T{game.display_turn}: {creature.name} modular — "
                    f"move {_modular_counters} counter(s) to {_target.name}"
                )

        # Dies triggers: a registered EffectTiming.DIES handler owns
        # this card's dies behavior (mirrors the ETB execute-then-
        # fallback pattern at card_effects.py:69); only fall back to
        # the generic oracle-derived path when no DIES-specific
        # handler is registered.
        if not EFFECT_REGISTRY.execute(creature.template.name, EffectTiming.DIES,
                                       game, creature, controller):
            from .oracle_resolver import resolve_dies_trigger
            resolve_dies_trigger(game, creature, controller)

        # Observers on either battlefield ("whenever a/another creature
        # [you control] dies, …", CR 603.2) — dispatched after the zone
        # move, so the dying creature itself is never an observer of its
        # own death and the look-back (CR 603.10) needs no special case.
        PermanentEffects._fire_creature_dies_observers(game, creature, controller)

        game.log.append(f"T{game.display_turn}: {creature.name} dies")

    @staticmethod
    def _fire_creature_dies_observers(game: "GameState", creature: CardInstance,
                                      controller: int) -> None:
        """Fan one creature's death out to every typed observer
        (`CardTemplate.creature_dies_observer`) whose scope matches the
        dying creature's controller. Effects: a +1/+1 counter on the
        observer's attached creature or on itself (subtype bonus honoured),
        drain, gain, draw — through the engine's own funnels (the counter
        funnel fires counters-placed triggers; life loss reaches the SBA)."""
        fired: set = set()
        for p_idx, player in enumerate(game.players):
            for perm in list(player.battlefield):
                spec = perm.template.creature_dies_observer
                if not spec or perm.zone != "battlefield" \
                        or not _prints_observer(perm, spec):
                    continue
                if spec["scope"] == "you" and controller != p_idx:
                    continue
                if spec["scope"] == "opponent" and controller == p_idx:
                    continue
                if not observer_matches(spec, creature):
                    continue
                fired.add(id(perm))
                kind = spec["kind"]
                if kind in ("counter_attached", "counter_self"):
                    target = perm
                    if kind == "counter_attached":
                        # The bearer is tagged `equipped_{equipment id}`
                        # by `GameState.attach_equipment` (one owner);
                        # Auras record `attached_to_id` instead.
                        tag = f"equipped_{perm.instance_id}"
                        target = next(
                            (c for c in player.battlefield
                             if tag in getattr(c, "instance_tags", ())), None)
                        if target is None:
                            target = game.get_card_by_id(
                                getattr(perm, "attached_to_id", None) or -1)
                    if target is None or target.zone != "battlefield":
                        continue
                    amount = spec["amount"]
                    bonus = spec.get("subtype_bonus")
                    if bonus and bonus[0] in {s.lower() for s in (target.template.subtypes or [])}:
                        amount = bonus[1]
                    target.add_plus_counters(amount, game, source=perm)
                    game.log.append(
                        f"T{game.display_turn} P{p_idx+1}: {perm.name} — "
                        f"{creature.name} died → +{amount}/+{amount} counter(s) on {target.name}")
                elif kind == "drain":
                    from .damage import lose_life
                    for o_idx in range(len(game.players)):
                        if o_idx != p_idx:
                            lose_life(game, o_idx, spec["amount"])
                    if spec["gain"]:
                        PermanentEffects.gain_life(game, p_idx, spec["gain"])
                    game.log.append(
                        f"T{game.display_turn} P{p_idx+1}: {perm.name} — "
                        f"{creature.name} died → each opponent loses {spec['amount']} life")
                    game.check_state_based_actions()
                elif kind == "gain":
                    PermanentEffects.gain_life(game, p_idx, spec["gain"])
                    if spec["draw"]:
                        game.draw_cards(p_idx, spec["draw"])
                elif kind == "draw":
                    game.draw_cards(p_idx, spec["draw"])
                    if spec["lose_life"]:
                        from .damage import lose_life
                        lose_life(game, p_idx, spec["lose_life"])
                        game.check_state_based_actions()
                elif kind == "transform_self":
                    # "You may exile <this>, then return it to the
                    # battlefield transformed": a new object (CR 400.7)
                    # through the zone funnel. The engine performs the
                    # optional exile, as the resolver it replaces did.
                    from .oracle_resolver import _transform_permanent
                    _transform_permanent(game, perm, p_idx,
                                         returns_as_new_object=True)
                if game.game_over:
                    return
        _audit_dies_observers(game, creature, controller, fired)


    @staticmethod
    def _permanent_destroyed(game: "GameState", permanent: CardInstance):
        if permanent.template.is_creature:
            game._creature_dies(permanent)
        else:
            game.zone_mgr.move_card(
                game, permanent, "battlefield", "graveyard",
                cause="destroyed"
            )


    @staticmethod
    def _exile_permanent(game: "GameState", permanent: CardInstance):
        game.zone_mgr.move_card(
            game, permanent, "battlefield", "exile",
            cause="exiled"
        )


    @staticmethod
    def _bounce_permanent(game: "GameState", permanent: CardInstance):
        game.zone_mgr.move_card(
            game, permanent, "battlefield", "hand",
            cause="bounced"
        )


    @staticmethod
    def gain_life(game: "GameState", player_idx: int, amount: int, source: str = ""):
        """Centralized lifegain with triggers (Ocelot Pride, etc.)."""
        if amount <= 0:
            return
        player = game.players[player_idx]
        player.life += amount
        player.life_gained_this_turn += amount
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                        f"Gain {amount} life from {source} (life: {player.life})")
        # Generic "whenever you gain life, create a token" triggers — typed field,
        # no runtime oracle-text inspection (see CardTemplate.has_lifegain_token_trigger).
        for creature in list(player.creatures):
            if creature.template.has_lifegain_token_trigger:
                game.create_token(player_idx, creature.template.lifegain_token_type, count=1)
                break  # once per lifegain event

    # ─── SPELL EFFECTS ───────────────────────────────────────────


    @staticmethod
    def produce_energy(game: "GameState", player_idx: int, amount: int, source_name: str = ""):
        """Add energy counters to a player."""
        game.players[player_idx].add_energy(amount)
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                        f"+{amount} energy from {source_name} "
                        f"(total: {game.players[player_idx].energy_counters})")


    @staticmethod
    def spend_energy_for_effect(game: "GameState", player_idx: int, amount: int,
                                 effect_type: str = "") -> bool:
        """Spend energy for an effect. Returns True if successful."""
        if game.players[player_idx].spend_energy(amount):
            game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                            f"Spend {amount} energy for {effect_type}")
            return True
        return False

