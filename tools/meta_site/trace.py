"""Trace a few cards through the effect pipeline, for the showcase.

Writes tools/meta_site/content/trace.json, which build_data.py reads like
the other content files. Every value is read from the engine itself, at the
commit the trace is generated at:

  * the printed text of each card named in content/architecture.json
    ("trace": [{"card", "why"}]);
  * each host of its front face as the grammar typed it at load -- kind,
    trigger head, and the instructions in printed order with their
    branches ("then", "otherwise", "instead");
  * which carrier takes the host and the family whose strict view and
    executors take it (`effect_carrier` plans, `spell_family`,
    `mode_family`, `activation_family`, `loyalty_family`), or, for a host
    no carrier takes, the strict-view and `can_execute` verdicts that keep
    it on its legacy path;
  * the executor each instruction's verb runs (`effect_resolver.EXECUTORS`)
    and whether its `supports` admits the shape;
  * the sizes of the engine's vocabulary (verbs, host kinds, amount and
    condition kinds, strict views, executor verbs, sub-grammars).

Needs the card database (ModernAtomic.json). Re-run after any change to the
grammar or the carriers, then rebuild the site:

    python -m tools.meta_site.trace
    python -m tools.meta_site.build_data && python -m tools.meta_site.render
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTENT = Path(__file__).resolve().parent / "content"
OUT = CONTENT / "trace.json"

# The trigger carriers, in the order the page lists them: (label, plan
# function name in engine.effect_carrier, the families it tries).
_TRIGGER_CARRIERS = (
    ("enter", "etb_plan"),
    ("attack", "attack_plan"),
    ("cast", "cast_plan"),
    ("step", "step_plan"),
    ("draw", "draw_plan"),
    ("combat damage", "combat_damage_plan"),
)


def _name(v: Any) -> str:
    return getattr(v, "name", str(v))


def _selector(sel: Any) -> str:
    """Who acts: a PLAYER selector naming no one is the ability's
    controller, the printed "you"."""
    if sel is None:
        return ""
    kind = _name(getattr(sel, "kind", ""))
    player = getattr(sel, "player", None)
    if kind == "PLAYER" and not player and getattr(sel, "obj", None) is None:
        return "you (its controller)"
    kind = kind.lower().replace("_", " ")
    return f"{kind} ({player})" if player else kind


def _filter(f: Any) -> str:
    if f is None:
        return ""
    raw = getattr(f, "raw", "") or ""
    keys = []
    for k in ("zone", "types", "all_types", "subtypes", "supertypes",
              "not_types", "colors", "position"):
        v = getattr(f, k, None)
        if v:
            shown = ", ".join(sorted(str(x) for x in v)) \
                if isinstance(v, (frozenset, set, tuple)) else v
            keys.append(f"{k.replace('_', ' ')} {shown}")
    bounds = getattr(f, "stat_bounds", ()) or ()
    for b in bounds:
        keys.append(" ".join(str(_name(x)) for x in (b if isinstance(b, tuple) else (b,))))
    typed = "; ".join(keys)
    return f'"{raw}" ({typed})' if raw and typed else raw or typed


def _amount(a: Any) -> str:
    if a is None:
        return ""
    kind = _name(getattr(a, "kind", "")).lower().replace("_", " ")
    n = getattr(a, "n", None)
    return f"{kind} {n}" if n is not None else kind


def _dest(d: Any) -> str:
    if d is None:
        return ""
    bits = [str(getattr(d, "zone", "") or "")]
    if getattr(d, "tapped", False):
        bits.append("tapped")
    if getattr(d, "position", None):
        bits.append(str(d.position))
    ctl = getattr(d, "controller", "default")
    if ctl and ctl != "default":
        bits.append(f"under {ctl}")
    return " ".join(b for b in bits if b)


def _condition(c: Any) -> str:
    if c is None:
        return ""
    kind = _name(getattr(c, "kind", "")).lower()
    pred = getattr(c, "pred", "") or ""
    raw = getattr(c, "raw", "") or ""
    return raw or f"{kind} {pred}".strip()


def _ref(r: Any) -> str:
    if r is None:
        return ""
    kind = _name(getattr(r, "kind", "")).lower()
    idx = getattr(r, "index", None)
    return f"{kind} of #{idx}" if idx is not None else kind


def _target(t: Any) -> str:
    if t is None:
        return ""
    return getattr(t, "raw_phrase", "") or str(getattr(t, "zone", ""))


def _payload(p: Any) -> str:
    """The typed payload in a few words: a sub-ability's kind, counters'
    kinds, a token's characteristics, mana symbols, a keyword action, or
    where the grammar stopped (UNMODELLED: stage and detail code)."""
    if p is None:
        return ""
    cls = type(p).__name__
    if hasattr(p, "host") and hasattr(p, "timing"):
        timing = getattr(p, "timing", None)
        return f"{_name(p.kind).lower()} trigger" + (
            f" ({_name(timing).lower().replace('_', ' ')})" if timing else "")
    if cls == "CounterSpec":
        return "counters: " + ", ".join(p.kinds or ("any kind",))
    if cls == "TokenSpec":
        if p.predefined:
            return f"token: {p.predefined}"
        pt = (f"{_amount(p.power)}/{_amount(p.toughness)} "
              if p.power is not None else "")
        return f"token: {pt}{' '.join(p.subtypes)} {' '.join(p.types)}".strip()
    if cls == "ManaSpec":
        return "mana: " + ("".join(p.symbols) or "any colour")
    if cls == "KeywordAction":
        return f"keyword action: {p.name}"
    if cls == "Unmodelled":
        return f"not modelled at {_name(p.stage)}" + (
            f" ({p.detail})" if p.detail else "")
    return cls


def _executor(s: Any) -> Dict[str, Any]:
    from engine import effect_resolver as er
    from engine.effect_spec import Verb
    if s.verb is Verb.CREATE_TRIGGER:
        return {"name": "sub-ability host", "supports": True}
    ex = er.EXECUTORS.get(s.verb)
    if ex is None:
        return {"name": "", "supports": False}
    sup = getattr(ex, "supports", None)
    try:
        ok = bool(sup(s)) if sup is not None else True
    except Exception:          # a malformed shape is simply not supported
        ok = False
    return {"name": getattr(ex, "__name__", type(ex).__name__), "supports": ok}


def spec_view(s: Any) -> Dict[str, Any]:
    """One instruction as the page shows it: verb, printed words, the typed
    fields it carries, its executor, and its branches."""
    fields = []
    for label, text in (("actor", _selector(s.actor)),
                        ("target", _target(s.target)),
                        ("finds", _filter(s.filter)),
                        ("amount", _amount(s.amount)),
                        ("to", _dest(s.dest)),
                        ("reads", _ref(s.ref)),
                        ("if", _condition(s.condition)),
                        ("creates", _payload(s.payload))):
        if text:
            fields.append([label, text])
    if s.optional:
        fields.append(["choice", "optional (you may)"])
    if s.replaces:
        fields.append(["instead of", ", ".join(f"#{i}" for i in s.replaces)])
    if s.duration is not None:
        fields.append(["lasts", _name(getattr(s.duration, "kind", s.duration)).lower().replace("_", " ")])
    if s.flags:
        fields.append(["flags", ", ".join(sorted(s.flags))])
    out = {"seq": s.seq, "verb": _name(s.verb), "raw": s.raw or "",
           "fields": fields, "executor": _executor(s)}
    for branch in ("then", "otherwise"):
        specs = getattr(s, branch, ()) or ()
        if specs:
            out[branch] = [spec_view(x) for x in specs]
    alts = getattr(s, "alternatives", ()) or ()
    if alts:
        out["alternatives"] = [spec_view(getattr(a, "spec", a)) for a in alts
                               if getattr(getattr(a, "spec", a), "verb", None) is not None]
    return out


def _trigger_head(h: Any) -> Optional[Dict[str, Any]]:
    head = getattr(h, "trigger", None)
    if head is None:
        return None
    typed = [k for k in ("attack", "cast", "step_event", "draw", "combat_damage")
             if getattr(head, k, None) is not None]
    return {"raw": head.raw, "events": [_name(e) for e in head.event_hints],
            "typed": typed,
            "intervening_if": bool(getattr(head, "intervening_if", None))}


def _relevant_trigger_carriers(h: Any) -> List[str]:
    from engine.effect_spec import EventHint
    head = h.trigger
    out = []
    hints = set(head.event_hints)
    if EventHint.SELF_ENTERS in hints:
        out.append("enter")
    if head.attack is not None or EventHint.SELF_ATTACKS in hints:
        out.append("attack")
    if head.cast is not None:
        out.append("cast")
    if EventHint.BEGINNING_OF in hints:
        out.append("step")
    if head.draw is not None:
        out.append("draw")
    if head.combat_damage is not None:
        out.append("combat damage")
    return out


def _family_verdicts(h: Any, families) -> List[Dict[str, Any]]:
    from engine import effect_resolver as er
    from engine.effect_views import STRICT
    out = []
    for f in families:
        strict = STRICT.get(f)
        out.append({"family": f,
                    "strict": bool(strict(h)) if strict is not None else False,
                    "executable": bool(er.can_execute(h, f))})
    return out


def _carrier_families() -> Dict[str, tuple]:
    from engine import effect_carrier as ec
    return {"enter": tuple(ec.ETB_FAMILIES),
            "attack": (ec.ATTACK_FAMILY,), "cast": (ec.CAST_FAMILY,),
            "step": (ec.STEP_FAMILY,), "draw": (ec.DRAW_FAMILY,),
            "combat damage": (ec.COMBAT_DAMAGE_FAMILY,)}


def host_route(template: Any, face: tuple, h: Any) -> Dict[str, Any]:
    """Which carrier takes `h`, in which family; or the verdicts that keep
    it on its legacy path."""
    from engine import effect_carrier as ec
    from engine.effect_spec import HostKind
    kind = h.kind
    if kind is HostKind.TRIGGERED and h.trigger is not None:
        taken = []
        for label, fn in _TRIGGER_CARRIERS:
            plan = getattr(ec, fn)(face)
            for host, family in plan or ():
                if host is h:
                    taken.append({"carrier": label, "family": family})
        if taken:
            return {"taken": taken}
        relevant = _relevant_trigger_carriers(h)
        fams = _carrier_families()
        return {"taken": [], "carriers": relevant,
                "verdicts": [v for c in relevant for v in
                             _family_verdicts(h, fams.get(c, ()))]}
    if kind is HostKind.SPELL:
        fam = ec.spell_family(template)
        return ({"taken": [{"carrier": "spell", "family": fam}]} if fam else
                {"taken": [], "carriers": ["spell"],
                 "verdicts": _family_verdicts(h, ec.SPELL_FAMILIES)})
    if kind is HostKind.MODE and h.mode_index >= 0:
        fam = ec.mode_family(template, h.mode_index)
        return ({"taken": [{"carrier": "spell mode", "family": fam}]} if fam
                else {"taken": [], "carriers": ["spell mode"],
                      "verdicts": _family_verdicts(h, ec.SPELL_FAMILIES)})
    if kind is HostKind.ACTIVATED:
        acts = {a.index: a for a in template.activated_abilities or ()}
        ab = acts.get(h.activation_index)
        fam = ec.activation_family(ab, h) if ab is not None else None
        return ({"taken": [{"carrier": "activation", "family": fam}]} if fam
                else {"taken": [], "carriers": ["activation"],
                      "legacy_kind": _name(getattr(ab, "effect_kind", None))
                      if ab is not None else ""})
    if kind is HostKind.LOYALTY:
        abilities = getattr(template, "loyalty_abilities", None) or {}
        ab = abilities.get(h.loyalty_slot)
        fam = ec.loyalty_family(ab, h) if ab is not None else None
        return ({"taken": [{"carrier": "loyalty", "family": fam}]} if fam
                else {"taken": [], "carriers": ["loyalty"],
                      "legacy_kind": _name(getattr(ab, "effect_kind", None))
                      if ab is not None else ""})
    return {"taken": [], "carriers": []}


def _mode_views(h: Any) -> List[Dict[str, Any]]:
    """A modal triggered ability's modes, each as the host of its own the
    dispatcher performs once chosen (`effect_carrier.trigger_mode_host`:
    the parent's head, the mode's instructions as chosen)."""
    from engine import effect_carrier as ec
    out = []
    for i, m in enumerate(getattr(h, "modes", ()) or ()):
        run = ec.trigger_mode_host(h, i) if h.trigger is not None else None
        specs = (run or m).specs
        out.append({"index": i, "text": m.text,
                    "specs": [spec_view(s) for s in specs]})
    return out


def trace_card(db: Any, name: str) -> Optional[Dict[str, Any]]:
    template = db.get_card(name)
    if template is None:
        return None
    face = tuple(template.effects.faces[0])
    hosts = []
    for h in face:
        entry = {"kind": _name(h.kind), "index": h.index, "text": h.text,
                 "head": _trigger_head(h),
                 "keywords": [k.name for k in getattr(h, "keywords", ()) or ()],
                 "specs": [spec_view(s) for s in h.specs],
                 "modes": _mode_views(h),
                 "choose": list(getattr(h, "choose", ()) or ()),
                 "route": host_route(template, face, h)}
        hosts.append(entry)
    return {"card": template.name, "oracle": template.oracle_text or "",
            "hosts": hosts}


def vocabulary() -> Dict[str, Any]:
    from engine import effect_carrier as ec
    from engine import effect_resolver as er
    from engine import effect_executors as ex
    from engine.effect_spec import AmountKind, ConditionKind, HostKind, Verb
    from engine.effect_views import STRICT
    subs = sorted(p.stem for p in (ROOT / "engine" / "effect_grammar" / "sub").glob("*.py")
                  if p.stem != "__init__")
    return {"verbs": len(Verb), "host_kinds": len(HostKind),
            "amount_kinds": len(AmountKind), "condition_kinds": len(ConditionKind),
            "strict_views": sorted(STRICT), "executor_verbs": sorted(v.name for v in er.EXECUTORS),
            "condition_evaluators": sorted(k.name for k in er.CONDITION_EVALUATORS),
            "families": {k: len(v) for k, v in ex.FAMILIES.items()},
            "enter_families": list(ec.ETB_FAMILIES),
            "spell_families": list(ec.SPELL_FAMILIES),
            "sub_grammars": subs}


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_trace(db: Any, cards: List[Dict[str, str]]) -> Dict[str, Any]:
    traced = []
    for item in cards:
        t = trace_card(db, item["card"])
        if t is not None:
            t["why"] = item.get("why", "")
            traced.append(t)
    return {"commit": _commit(), "cards": traced, "vocabulary": vocabulary()}


def main(argv=None) -> int:
    arch = json.loads((CONTENT / "architecture.json").read_text())
    cards = arch.get("trace") or []
    from engine.card_database import CardDatabase
    with contextlib.redirect_stdout(io.StringIO()):
        db = CardDatabase()
    data = build_trace(db, cards)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(data['cards'])} cards at {data['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
