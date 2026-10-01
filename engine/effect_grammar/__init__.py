"""The clause and trigger grammar (design doc 2026-09-29, section 3).

The single parse-once owner of clause structure: it reads oracle text
through the per-template `CardTemplate.effects` memo -- lazily, on a
template's first access (which may fall mid-game), never at resolution --
and produces the typed `engine.effect_spec` model. Layers L0-L5 live in sibling modules; the closed sub-grammars live
in `engine.effect_grammar.sub`.

**Entry points** (section 3):

* `parse_template(template, facts=None) -> CardEffects` -- every face of a
  card template (face 0 the oracle text, face 1 the back face);
* `parse_face(text, facts, face=0) -> Tuple[AbilityEffects, ...]` -- one
  face's hosts, memoised on the complete fact key (A32);
* `parse_effects(text, host=HostKind.SPELL) -> Tuple[EffectSpec, ...]` --
  the specs of the first host of that kind in a synthetic text;
* `parse_text_effects(oracle, facts=None) -> CardEffects` -- a one-face
  card from bare text;
* `printed_span(oracle, facts, face, host_index, span) -> str` -- the
  printed text behind a host span, the L0 offset map recomputed for the
  call (A40) through `normalize.printed_span`;
* `clear_caches()` -- the module memos only; the per-template
  `CardTemplate.effects` memos live as long as their templates
  (`CardTemplate.set_effects(None)` clears one).

`template_facts` builds a face's `normalize.Facts` from a template (the
section-3 fact list), `template_inputs` the complete parse input the
`CardTemplate.effects` memo is keyed on, and `parse_pool` is the eager whole-pool path the
tools use; a game parses lazily, per template, through `parse_template`.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Tuple

__all__ = ["parse_template", "parse_face", "parse_effects",
           "parse_text_effects", "printed_span", "template_facts",
           "template_inputs", "parse_pool", "clear_caches"]

_SPELL_TYPES = frozenset({"instant", "sorcery"})


def _type_names(types) -> frozenset:
    return frozenset(getattr(t, "value", str(t)) for t in types or ())


def template_facts(template, face: int = 0,
                   keywords: Optional[Iterable[str]] = None):
    """The section-3 face facts of one template face: the self names
    (full name, face names, the legendary / character short name; a meld
    card's melded-permanent half is none, CR 712.4, read from the printed
    layout `CardTemplate.layout`), the
    face's card types, spell / legendary / planeswalker / X-cost facts and
    the face's MTGJSON keywords intersected with the CR 702 table
    (`keywords`, default the template's printed MTGJSON list,
    `CardTemplate.printed_keywords`). The typed `CardTemplate.keywords`
    enum is never read: it omits keywords the engine does not model and
    holds granted ones, so a parse from it would differ from the eager
    pool path's."""
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar.keywords import keywords702
    legendary = any(getattr(s, "value", s) == "legendary"
                    for s in getattr(template, "supertypes", ()) or ())
    if face == 0:
        tc = _type_names(getattr(template, "card_types", ()))
        subs = tuple(getattr(template, "subtypes", ()) or ())
        mc = getattr(template, "mana_cost", None)
        has_x = bool(getattr(mc, "x_count", 0))
        if keywords is None:
            keywords = getattr(template, "printed_keywords", ()) or ()
    else:
        tc = _type_names(getattr(template, "back_face_types", ()))
        subs = tuple(getattr(template, "back_face_subtypes", ()) or ())
        has_x = False
        keywords = () if keywords is None else keywords
    name = getattr(template, "name", "") or ""
    character = "planeswalker" in tc or (legendary and "creature" in tc)
    return N.Facts(
        names=N.self_names(name, is_legendary=legendary,
                           is_character=character,
                           subtypes=subs if "creature" in tc else (),
                           meld=getattr(template, "layout", "") == "meld"),
        type_class=tc,
        is_spell=bool(_SPELL_TYPES & tc),
        is_legendary=legendary,
        is_planeswalker="planeswalker" in tc,
        has_x_cost=has_x,
        keywords702=keywords702(keywords or ()))


def _face_texts(template) -> Tuple[str, ...]:
    front = getattr(template, "oracle_text", "") or ""
    back = getattr(template, "back_face_oracle", "") or ""
    return (front, back) if back else (front,)


def template_inputs(template):
    """The complete parse input of `template`: ``(key, facts)`` where
    `facts` lists `template_facts` per printed face and `key` is
    ``(name, face texts, facts)``. `CardTemplate.effects` keys its memo on
    `key` (A32's rule one level up), so a change to any field a face's
    facts read -- types, supertypes, subtypes, X cost, printed keywords,
    the back face's types -- parses again."""
    texts = _face_texts(template)
    facts = tuple(template_facts(template, i) for i in range(len(texts)))
    return (getattr(template, "name", "") or "", texts, facts), facts


def parse_face(text: str, facts=None, face: int = 0):
    """L0-L5 for one face's printed text: its `AbilityEffects` hosts in
    printed order, memoised on ``(text, facts, face)``."""
    from engine.effect_grammar import link
    from engine.effect_grammar import normalize as N
    return link.parse_face_hosts(text or "", facts or N.Facts(), face)


def parse_template(template, facts=None):
    """Every face of `template` as one `CardEffects`. `facts` is a face-0
    `Facts`, a per-face sequence of them, or None (derived by
    `template_facts`)."""
    from engine.effect_spec import CardEffects
    texts = _face_texts(template)
    faces = []
    for i, text in enumerate(texts):
        if isinstance(facts, (list, tuple)) and facts and \
                not hasattr(facts, "_fields"):
            f = facts[i] if i < len(facts) else template_facts(template, i)
        elif facts is not None and i == 0:
            f = facts
        else:
            f = template_facts(template, i)
        faces.append(parse_face(text, f, i) if text else ())
    return CardEffects.of(faces)


def _synthetic_facts(host):
    from engine.effect_grammar import normalize as N
    from engine.effect_spec import HostKind
    spell = host in (HostKind.SPELL, HostKind.MODE)
    return N.Facts(type_class=frozenset({"instant"}) if spell
                   else frozenset({"artifact"}), is_spell=spell)


def parse_effects(text: str, host=None):
    """The specs of the first host of kind `host` (default SPELL) in a
    synthetic text: a SPELL / MODE text reads as an instant, any other as
    a permanent's ability. () when no host of that kind is printed."""
    from engine.effect_spec import HostKind
    host = HostKind.SPELL if host is None else host
    for h in parse_face(text, _synthetic_facts(host)):
        if h.kind is host:
            return h.specs
        for m in h.modes:
            if m.kind is host:
                return m.specs
    return ()


def parse_text_effects(oracle: str, facts=None):
    """A one-face card from bare oracle text (synthetic templates, tests):
    `facts` default to a nameless instant."""
    from engine.effect_spec import CardEffects, HostKind
    return CardEffects.of(
        (parse_face(oracle, facts or _synthetic_facts(HostKind.SPELL), 0),))


def printed_span(oracle, facts, face: int, host_index: int,
                 span: Tuple[int, int]) -> str:
    """The printed text behind `span` of host `host_index` of face `face`
    (A40). `oracle` is the face's printed text, or the per-face sequence
    of texts. The host's parts locate the span in its paragraphs, and the
    L0 offset map -- recomputed for this call, never stored -- maps it to
    the printed text (`normalize.printed_span`)."""
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar import structure as S
    text = oracle[face] if isinstance(oracle, (list, tuple)) else oracle
    text = text or ""
    facts = facts or N.Facts()
    fs = S.parse_face_structure(text, facts, face)
    host = fs.hosts[host_index]
    starts, pos = [], 0
    paragraphs = fs.normalized.paragraphs
    for p in paragraphs:
        starts.append(pos)
        pos += len(p) + 1
    a, b = span
    for part in host.parts or ():
        pa, pb = part.span
        if pa <= a and b <= pb:
            # A host piece is its whole paragraph or the paragraph's tail
            # (L1 classifies the text after a keyword list or an earlier
            # ability as hosts of their own), so it ends where the
            # paragraph ends.
            at = len(paragraphs[part.paragraph]) - (pb - pa)
            base = starts[part.paragraph] + max(at, 0) - pa
            return N.printed_span(text, facts, (a + base, b + base))
    p = host.paragraphs[0] if host.paragraphs else 0
    base = starts[p] if p < len(starts) else 0
    return N.printed_span(text, facts, (a + base, b + base))


def parse_pool(db, *, keywords_of=None, populate: bool = False
               ) -> Dict[str, object]:
    """The eager pool path the tools use: every template of `db` parsed,
    keyed by name, through the same `parse_template(t)` call the lazy
    `CardTemplate.effects` property makes (the facts come from
    `template_facts`, the keywords from `CardTemplate.printed_keywords`);
    `keywords_of` may supply a face-0 keyword list instead. `populate`
    also pins each result on its template (`CardTemplate.set_effects`), so
    a tool that walks the whole pool reads `t.effects` without a second
    parse; it is refused with `keywords_of`, whose facts differ from the
    property's. Games never call this: they parse lazily, per template."""
    if populate and keywords_of is not None:
        raise ValueError("populate pins the property's own parse; "
                         "keywords_of changes its facts")
    out: Dict[str, object] = {}
    for t in {id(v): v for v in db.cards.values()}.values():
        if keywords_of is None:
            out[t.name] = parse_template(t)
            if populate:
                t.set_effects(out[t.name])
            continue
        facts = [template_facts(t, 0, keywords_of(t))]
        if getattr(t, "back_face_oracle", ""):
            facts.append(template_facts(t, 1))
        out[t.name] = parse_template(t, facts)
    return out


def clear_caches() -> None:
    """Clear the memo caches of every grammar module: the sub-grammars
    (`engine.effect_grammar.sub.clear_caches`) and the leaves that sit
    beside them (L0 normalize, the CR 701/702 keyword tables, the verb
    lexicon), the L1 structure memo, the L4 clause memo and the L5 face
    memo. Tools call it after a pool pass (`parse_pool`) to drop the
    module memos; no load pass calls it, since nothing parses at load.
    It does not clear the per-template `CardTemplate.effects` memos,
    which live as long as their templates (`set_effects(None)` clears
    one). The leaf-contract test pins that no module's cache is
    missed."""
    from engine.effect_grammar import (clauses, keywords, lexicon, link,
                                       normalize, patterns, structure, sub)
    sub.clear_caches()
    for leaf in (normalize, keywords, lexicon):
        leaf.clear_caches()
    for layer in (structure, clauses, patterns, link):
        layer.clear_caches()
