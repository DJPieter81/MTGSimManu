#!/usr/bin/env python3
"""Pool census of the clause grammar's refusals and typed share.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 9
(UNMODELLED and residue census) and section 17 (exit criterion 3).

Every template of the card pool is parsed through the eager tools' path
(`engine.effect_grammar.parse_pool`, the same `parse_template` call the lazy
`CardTemplate.effects` property makes; nothing is pinned on a template) and
every spec of every host -- sub-ability hosts included, granted hosts not,
the walk `CardEffects.walk` makes -- is counted:

* UNMODELLED specs by `(stage, lemma, detail)` and by stage;
* residue codes by code and by polarity (WIDENING / NARROWING / UNPARSED,
  `engine.effect_spec.RESIDUE_CODES`);
* the typed share (non-UNMODELLED specs) by host kind, overall and on the
  registered-deck cards (MODERN_DECKS mainboard and sideboard);
* the report rows section 9 adds: the `may_scope` nestings (one row per
  shape, A29), the sub-ability shapes (REFLEXIVE / DELAYED, with and without
  an intervening-if), the keyword-line classifications (a printed CR 702
  keyword line by the host kind it became, so a keyword line that fell to
  SPELL on any face is visible) and the `cost_modifiers` absorptions.

``--update`` writes `tools/effect_census_baseline.json` (the pinned totals
plus the report rows) and generates `docs/design/effect_grammar_census.md`
from it. ``--check`` re-runs the census and fails when the typed share
(overall, per host kind, on deck cards) falls or any refusal total (per
stage, per residue code, per polarity, on deck cards) grows; an improvement
passes and is reported, to be locked in with ``--update``.

Usage::

    python tools/effect_census.py            # print the census summary
    python tools/effect_census.py --check    # compare against the baseline
    python tools/effect_census.py --update   # rewrite baseline + census doc
    python tools/effect_census.py --json     # the full census as JSON
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BASELINE_PATH = REPO / "tools" / "effect_census_baseline.json"
DOC_PATH = REPO / "docs" / "design" / "effect_grammar_census.md"
DESIGN_DOC = "docs/design/2026-09-29_clause_and_trigger_grammar.md"

# Report rows kept per table in the baseline and the generated doc. The
# pinned totals are complete; the long tail of (stage, lemma, detail) rows
# is summarised by its total.
TOP_ROWS = 60
# Shares are compared at this many decimals, so a float round-trip
# through JSON never reads as a fall.
SHARE_DECIMALS = 4


# ── the census (pure: CardEffects in, dict out) ───────────────────────

def _share(typed: int, total: int) -> float:
    return round(typed / total, SHARE_DECIMALS) if total else 1.0


def _kind_table(tot: Mapping[str, int], typed: Mapping[str, int]) -> dict:
    return {k: {"specs": tot[k], "typed": typed.get(k, 0),
                "share": _share(typed.get(k, 0), tot[k])}
            for k in sorted(tot)}


def _verb(s) -> str:
    return s.verb.name


def _may_scope_shape(s) -> Optional[str]:
    """`VERB? > THEN, ...` for an optional spec whose then-branch holds a
    may_scope follower (A29); None otherwise."""
    nested = [t for t in s.then if "may_scope" in t.flags]
    if not nested:
        return None
    return f"{_verb(s)}? > " + ", ".join(_verb(t) for t in nested)


def _keyword_lines(face_hosts, keywords: Iterable[str]):
    """(keyword, host kind) for every host whose text opens with one of
    the face's printed CR 702 keywords."""
    kws = sorted({k.lower() for k in keywords or ()}, key=len, reverse=True)
    if not kws:
        return
    for h in face_hosts:
        text = (h.text or "").lower()
        for kw in kws:
            if text.startswith(kw) and (len(text) == len(kw)
                                        or not text[len(kw)].isalpha()):
                yield kw, h.kind.name
                break


def census(effects: Mapping[str, Any], *,
           deck_names: Iterable[str] = (),
           keywords_of=None) -> dict:
    """The census of `effects` ({card name: CardEffects}).

    `deck_names` are the registered-deck card names; `keywords_of(name,
    face)` returns that face's printed CR 702 keywords (the face facts'
    `keywords702`) for the keyword-line rows, or is None to skip them."""
    from engine.effect_spec import (SubAbility, Verb, iter_specs,
                                    residue_polarity)
    deck = set(deck_names)
    tot, typed = collections.Counter(), collections.Counter()
    dtot, dtyped = collections.Counter(), collections.Counter()
    um_rows, um_stage = collections.Counter(), collections.Counter()
    deck_um_stage = collections.Counter()
    residue, polarity = collections.Counter(), collections.Counter()
    deck_residue = collections.Counter()
    may_scope, sub_shapes = collections.Counter(), collections.Counter()
    kw_lines, cost_mods = collections.Counter(), collections.Counter()
    cards = deck_cards = 0
    for name in sorted(effects):
        ce = effects[name]
        on_deck = name in deck
        cards += 1
        deck_cards += on_deck
        for h in ce.walk():
            k = h.kind.name
            for m in h.cost_modifiers:
                cost_mods[(k, getattr(m.kind, "name", str(m.kind)))] += 1
            for s in iter_specs(h.specs):
                tot[k] += 1
                if on_deck:
                    dtot[k] += 1
                if s.verb is Verb.UNMODELLED:
                    p = s.payload
                    stage = getattr(getattr(p, "stage", None), "name", "?")
                    um_rows[(stage, getattr(p, "lemma", ""),
                             getattr(p, "detail", ""))] += 1
                    um_stage[stage] += 1
                    if on_deck:
                        deck_um_stage[stage] += 1
                else:
                    typed[k] += 1
                    if on_deck:
                        dtyped[k] += 1
                for code in s.residue:
                    residue[code] += 1
                    polarity[residue_polarity(code) or "UNKNOWN"] += 1
                    if on_deck:
                        deck_residue[code] += 1
                shape = _may_scope_shape(s)
                if shape:
                    may_scope[shape] += 1
                if isinstance(s.payload, SubAbility):
                    sub = s.payload
                    timing = getattr(sub.timing, "name", "") or "-"
                    trig = sub.host.trigger
                    iif = trig is not None and trig.intervening_if is not None
                    sub_shapes[(sub.kind.name, timing,
                                "intervening_if" if iif else "plain")] += 1
        if keywords_of is not None:
            for face, hosts in enumerate(ce.faces):
                for kw, kind in _keyword_lines(hosts, keywords_of(name, face)):
                    kw_lines[(kw, kind)] += 1
    total, n_typed = sum(tot.values()), sum(typed.values())
    d_total, d_typed = sum(dtot.values()), sum(dtyped.values())
    return {
        "cards": cards,
        "specs": total,
        "typed": n_typed,
        "typed_share": _share(n_typed, total),
        "by_host_kind": _kind_table(tot, typed),
        "deck": {"cards": deck_cards, "specs": d_total, "typed": d_typed,
                 "typed_share": _share(d_typed, d_total),
                 "by_host_kind": _kind_table(dtot, dtyped),
                 "unmodelled_by_stage": dict(sorted(deck_um_stage.items())),
                 "residue_by_code": dict(sorted(deck_residue.items()))},
        "unmodelled": sum(um_stage.values()),
        "unmodelled_by_stage": dict(sorted(um_stage.items())),
        "unmodelled_rows": [list(r) + [n] for r, n in sorted(
            um_rows.items(), key=lambda kv: (-kv[1], kv[0]))],
        "residue": sum(residue.values()),
        "residue_by_code": dict(sorted(residue.items())),
        "residue_by_polarity": dict(sorted(polarity.items())),
        "may_scope": [[shape, n] for shape, n in sorted(
            may_scope.items(), key=lambda kv: (-kv[1], kv[0]))],
        "sub_abilities": [list(r) + [n] for r, n in sorted(
            sub_shapes.items(), key=lambda kv: (-kv[1], kv[0]))],
        "keyword_lines": [list(r) + [n] for r, n in sorted(
            kw_lines.items(), key=lambda kv: (-kv[1], kv[0]))],
        "cost_modifiers": [list(r) + [n] for r, n in sorted(
            cost_mods.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


# ── the pool ──────────────────────────────────────────────────────────

def deck_card_names() -> frozenset:
    """Every registered-deck card name (mainboard and sideboard)."""
    from decks.modern_meta import MODERN_DECKS
    return frozenset(c for d in MODERN_DECKS.values()
                     for part in ("mainboard", "sideboard")
                     for c in (d.get(part) or {}))


def pool_templates(db) -> List[Any]:
    """The distinct templates of `db`, in name order."""
    return sorted({id(t): t for t in db.cards.values()}.values(),
                  key=lambda t: t.name)


def _deck_template_names(db) -> frozenset:
    names = deck_card_names()
    return frozenset(t.name for n in names
                     for t in (db.cards.get(n),) if t is not None)


def pool_census(db, effects: Optional[Mapping[str, Any]] = None) -> dict:
    """The census of every template of `db`. `effects` may supply the
    eager pool parse (`parse_pool(db)`) when the caller already has it."""
    import engine.effect_grammar as grammar
    if effects is None:
        effects = grammar.parse_pool(db)
    by_name = {t.name: t for t in pool_templates(db)}

    def keywords_of(name, face):
        t = by_name.get(name)
        return () if t is None else grammar.template_facts(t, face).keywords702

    return census(effects, deck_names=_deck_template_names(db),
                  keywords_of=keywords_of)


# ── baseline and check ────────────────────────────────────────────────

def _top(rows: List[list], n: int = TOP_ROWS) -> List[list]:
    return [list(r) for r in rows[:n]]


def baseline_of(c: Mapping[str, Any]) -> dict:
    """What `--update` writes: the pinned totals and the report rows the
    generated doc renders (the long tail summarised by its total)."""
    pinned = {
        "typed_share": c["typed_share"],
        "typed_share_by_host_kind": {k: v["share"]
                                     for k, v in c["by_host_kind"].items()},
        "deck_typed_share": c["deck"]["typed_share"],
        "unmodelled_by_stage": dict(c["unmodelled_by_stage"]),
        "residue_by_code": dict(c["residue_by_code"]),
        "residue_by_polarity": dict(c["residue_by_polarity"]),
        "deck_unmodelled_by_stage": dict(c["deck"]["unmodelled_by_stage"]),
        "deck_residue_by_code": dict(c["deck"]["residue_by_code"]),
    }
    report = {
        "cards": c["cards"], "specs": c["specs"], "typed": c["typed"],
        "unmodelled": c["unmodelled"], "residue": c["residue"],
        "by_host_kind": c["by_host_kind"],
        "deck": {k: c["deck"][k] for k in ("cards", "specs", "typed",
                                           "by_host_kind")},
        "unmodelled_rows": _top(c["unmodelled_rows"]),
        "unmodelled_row_count": len(c["unmodelled_rows"]),
        "may_scope": [list(r) for r in c["may_scope"]],
        "sub_abilities": [list(r) for r in c["sub_abilities"]],
        "keyword_lines": [list(r) for r in c["keyword_lines"]],
        "cost_modifiers": [list(r) for r in c["cost_modifiers"]],
    }
    return {"description": (
        "Clause-grammar census (design doc 2026-09-29 section 9): pinned "
        "typed shares may only rise, pinned refusal totals may only fall. "
        "Regenerate with `python tools/effect_census.py --update`."),
        "pinned": pinned, "report": report}


def compare(baseline: Mapping[str, Any], current: Mapping[str, Any]
            ) -> Tuple[List[str], List[str]]:
    """(regressions, improvements) of the census `current` against the
    pinned `baseline` (both `baseline_of` shapes, or `current` a census)."""
    if "pinned" not in current:
        current = baseline_of(current)
    b, c = baseline["pinned"], current["pinned"]
    bad, good = [], []

    def share(label, old, new):
        if new < old:
            bad.append(f"{label}: typed share fell {old:.4f} -> {new:.4f}")
        elif new > old:
            good.append(f"{label}: typed share rose {old:.4f} -> {new:.4f}")

    def count(label, old, new):
        if new > old:
            bad.append(f"{label}: grew {old} -> {new}")
        elif new < old:
            good.append(f"{label}: fell {old} -> {new}")

    share("pool", b["typed_share"], c["typed_share"])
    share("deck cards", b["deck_typed_share"], c["deck_typed_share"])
    for k in sorted(set(b["typed_share_by_host_kind"])
                    | set(c["typed_share_by_host_kind"])):
        share(f"host kind {k}", b["typed_share_by_host_kind"].get(k, 1.0),
              c["typed_share_by_host_kind"].get(k, 1.0))
    for table, label in (("unmodelled_by_stage", "UNMODELLED stage"),
                         ("residue_by_code", "residue code"),
                         ("residue_by_polarity", "residue polarity"),
                         ("deck_unmodelled_by_stage",
                          "deck-card UNMODELLED stage"),
                         ("deck_residue_by_code", "deck-card residue code")):
        old, new = b[table], c[table]
        for k in sorted(set(old) | set(new)):
            count(f"{label} {k}", old.get(k, 0), new.get(k, 0))
    return bad, good


# ── the generated doc ─────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _table(header: List[str], rows: Iterable[Iterable[Any]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(
            str(x).replace("|", "\\|") if x != "" else "-" for x in r) + " |")
    return out


def render_markdown(base: Mapping[str, Any]) -> str:
    """The census doc, generated from a `baseline_of` value alone (so a
    test can regenerate it from the committed baseline without the pool)."""
    p, r = base["pinned"], base["report"]
    deck = r["deck"]
    session = base.get("session", "")
    lines = [
        "---",
        'title: "Clause-grammar census: UNMODELLED, residue and typed share"',
        "status: active",
        "priority: diagnostic",
        f"session: {session}",
        f"depends_on: [{DESIGN_DOC}]",
        "tags: [engine, oracle, grammar, effects, census, generated]",
        "summary: >",
        f"  Generated by tools/effect_census.py --update (design doc section"
        f" 9). {r['specs']:,} specs over {r['cards']:,} templates,"
        f" {_pct(p['typed_share'])} typed; registered-deck cards"
        f" {_pct(p['deck_typed_share'])} typed over {deck['specs']:,} specs."
        f" {r['unmodelled']:,} UNMODELLED specs, {r['residue']:,} residue"
        f" codes. Do not edit by hand.",
        "---",
        "",
        "# Clause-grammar census",
        "",
        "Generated by `python tools/effect_census.py --update` from"
        " `tools/effect_census_baseline.json`; do not edit by hand."
        f" The design is [{DESIGN_DOC}](2026-09-29_clause_and_trigger_grammar.md),"
        " section 9. `--check` fails when a pinned typed share falls or a"
        " pinned refusal total grows.",
        "",
        "Every spec of every host is counted, sub-ability hosts included and"
        " granted hosts not. A spec is typed when its verb is not"
        " UNMODELLED.",
        "",
        "## Typed share by host kind",
        "",
    ]
    lines += _table(["Host kind", "Specs", "Typed", "Share", "Deck specs",
                     "Deck share"],
                    [(k, f"{v['specs']:,}", f"{v['typed']:,}",
                      _pct(v["share"]),
                      f"{deck['by_host_kind'].get(k, {}).get('specs', 0):,}",
                      _pct(deck["by_host_kind"][k]["share"])
                      if k in deck["by_host_kind"] else "-")
                     for k, v in r["by_host_kind"].items()])
    lines += ["", f"Pool: {r['specs']:,} specs, {r['typed']:,} typed"
              f" ({_pct(p['typed_share'])}). Registered-deck cards:"
              f" {deck['cards']:,} templates, {deck['specs']:,} specs,"
              f" {deck['typed']:,} typed ({_pct(p['deck_typed_share'])}).",
              "", "## UNMODELLED by stage", ""]
    lines += _table(["Stage", "Pool", "Deck cards"],
                    [(k, f"{n:,}", p["deck_unmodelled_by_stage"].get(k, 0))
                     for k, n in sorted(p["unmodelled_by_stage"].items(),
                                        key=lambda kv: (-kv[1], kv[0]))])
    lines += ["", f"## UNMODELLED by (stage, lemma, detail): top {TOP_ROWS}"
              f" of {r['unmodelled_row_count']:,} rows", ""]
    lines += _table(["Stage", "Lemma", "Detail", "Specs"],
                    r["unmodelled_rows"])
    lines += ["", "## Residue by polarity", ""]
    lines += _table(["Polarity", "Codes"],
                    sorted(p["residue_by_polarity"].items()))
    lines += ["", "## Residue by code", ""]
    lines += _table(["Code", "Pool", "Deck cards"],
                    [(k, n, p["deck_residue_by_code"].get(k, 0))
                     for k, n in sorted(p["residue_by_code"].items(),
                                        key=lambda kv: (-kv[1], kv[0]))])
    lines += ["", "## may_scope nestings (A29), one row per shape", ""]
    lines += _table(["Shape", "Specs"], r["may_scope"])
    lines += ["", "## Sub-ability shapes (A30)", ""]
    lines += _table(["Kind", "Timing", "Head", "Hosts"], r["sub_abilities"])
    lines += ["", "## Keyword-line classifications (A1)", "",
              "A host whose text opens with one of its face's printed CR 702"
              " keywords, by the host kind it became. A keyword line that"
              " fell to SPELL is listed here like any other.", ""]
    lines += _table(["Keyword", "Host kind", "Hosts"], r["keyword_lines"])
    lines += ["", "## cost_modifiers absorptions (A8)", ""]
    lines += _table(["Host kind", "Modification", "Hosts"],
                    r["cost_modifiers"])
    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────────────

def load_db():
    """The full card database, its load banner kept off stdout (so
    `--json` output parses)."""
    import contextlib
    import io
    from engine.card_database import CardDatabase
    with contextlib.redirect_stdout(io.StringIO()):
        return CardDatabase()


def _summary(c: Mapping[str, Any]) -> str:
    out = [f"templates {c['cards']:,}  specs {c['specs']:,}  typed "
           f"{_pct(c['typed_share'])}  deck cards {c['deck']['cards']}"
           f" typed {_pct(c['deck']['typed_share'])}"]
    for k, v in c["by_host_kind"].items():
        out.append(f"  {k:<18} {v['specs']:>7,}  {_pct(v['share'])}")
    out.append(f"UNMODELLED {c['unmodelled']:,}: " + ", ".join(
        f"{k} {n}" for k, n in sorted(c["unmodelled_by_stage"].items(),
                                      key=lambda kv: -kv[1])))
    out.append(f"residue {c['residue']:,}: " + ", ".join(
        f"{k} {n}" for k, n in c["residue_by_polarity"].items()))
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    ap.add_argument("--doc", type=Path, default=DOC_PATH)
    ap.add_argument("--session", default=None,
                    help="session date for the doc frontmatter "
                         "(default: today)")
    args = ap.parse_args(argv)
    c = pool_census(load_db())
    if args.json:
        print(json.dumps(c, indent=1, sort_keys=True))
    else:
        print(_summary(c))
    if args.update:
        import datetime
        base = baseline_of(c)
        base["session"] = args.session or datetime.date.today().isoformat()
        args.baseline.write_text(json.dumps(base, indent=1, sort_keys=True)
                                 + "\n")
        args.doc.write_text(render_markdown(base))
        print(f"wrote {args.baseline} and {args.doc}")
        return 0
    if args.check:
        base = json.loads(args.baseline.read_text())
        bad, good = compare(base, c)
        for g in good:
            print(f"improved: {g}")
        if good:
            print("(run `python tools/effect_census.py --update` to lock "
                  "the improvements in)")
        if bad:
            print("Effect census FAILED:")
            for b in bad:
                print(f"  {b}")
            return 1
        print("Effect census OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
