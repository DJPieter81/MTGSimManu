#!/usr/bin/env python3
"""Ratchet: the per-shape legacy effect parsers may only shrink.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 13
("tools/check_effect_parsers.py"). The clause grammar (`engine/effect_grammar`,
read through `CardTemplate.effects`) is the one parse-once owner of clause
structure; during the strangler (E1-E7) every legacy per-shape parser and
every runtime oracle read in a resolution handler is debt that a family
commit retires. This ratchet pins that debt. Each count may only FALL:

(a) `engine/card_database.py` `template.<f> = ...` assignments whose field
    has a FieldDerivation (`engine.effect_views.DERIVATIONS`) and is still
    populated by a legacy parser, not by an `effect_views` call;
(b) runtime oracle reads inside the resolution handlers -- in
    clause_resolver, oracle_resolver (resolve_self_cast_trigger included),
    spell_resolution, planeswalker_manager and triggers (the OracleTextParser
    description consumers): oracle-text / ability-description substring and
    regex tests (the `check_oracle_runtime_parse` data-flow detector, with
    `.description` and an ability's printed `.text` added), calls of an `oracle_parser.parse_*` function at
    resolution, and `host_for_override` lookups;
(c) the `_legacy_*` quirk predicates and `_legacy_domain_*` masks in
    `engine/effect_views.py` (`LEGACY_PREDICATES`);
(d) the total of `effect_resolver.LEGACY_RESIDUE_TOLERATED` codes (it may
    grow only in a family switch commit, with RESIDUE_WIDENING or
    RESIDUE_NARROWING evidence -- which lowers nothing here, so such a
    commit raises the baseline with that evidence in the same diff);
(e) a `def parse_*` in `engine/oracle_parser.py` whose result
    `card_database` assigns to a template field that is in neither
    DERIVATIONS nor NON_EFFECT_FIELDS -- pinned at 0: any is a failure;
(f) (handler, host) pairs on legacy fallback, as
    `tools/effect_spec_equivalence.py --gate-parity` reports them. It needs
    the card pool, so it runs only with ``--pool`` (the CI step after
    "Assemble card DB").

A count above its baseline is a regression; a count below it is a stale
baseline -- lower it in the same commit (``--update``), so the ceiling
cannot be silently re-filled.

Usage::

    python tools/check_effect_parsers.py            # (a)-(e), no card DB
    python tools/check_effect_parsers.py --pool     # also (f)
    python tools/check_effect_parsers.py --list     # every counted site
    python tools/check_effect_parsers.py --update [--pool]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASELINE_PATH = ROOT / "tools" / "effect_parsers_baseline.json"
CARD_DATABASE = ROOT / "engine" / "card_database.py"
ORACLE_PARSER = ROOT / "engine" / "oracle_parser.py"
# (b): the resolution-handler modules section 13 names, plus triggers.py,
# the OracleTextParser description consumer of the resolution layer.
HANDLER_MODULES = (
    "engine/clause_resolver.py",
    "engine/oracle_resolver.py",
    "engine/spell_resolution.py",
    "engine/planeswalker_manager.py",
    "engine/triggers.py",
)
COUNTS = ("a", "b", "c", "d", "e", "f")
STATIC_COUNTS = ("a", "b", "c", "d", "e")
LABELS = {
    "a": "card_database legacy-parser assignments to derived fields",
    "b": "runtime oracle reads in resolution handlers",
    "c": "_legacy_* quirk predicates and _legacy_domain_* masks",
    "d": "LEGACY_RESIDUE_TOLERATED codes",
    "e": "parse_* results assigned to an unaccounted field",
    "f": "(handler, host) pairs on legacy fallback",
}


# ── (a) and (e): card_database assignments ────────────────────────────

def _template_assignments(src: str) -> List[Tuple[int, str, ast.AST]]:
    """(line, field, value) for every `template.<f> = value`."""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            targets, value = [n.target], n.value
        else:
            continue
        for t in targets:
            for x in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(x, ast.Attribute) and \
                        isinstance(x.value, ast.Name) and \
                        x.value.id == "template":
                    out.append((n.lineno, x.attr, value))
    return out


def _calls(node: ast.AST) -> Set[str]:
    """Names of the functions `node` calls (bare or attribute)."""
    out = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
                if isinstance(f.value, ast.Name) and \
                        f.value.id == "effect_views":
                    out.add("effect_views." + f.attr)
    return out


def _is_view_call(value: ast.AST) -> bool:
    return any(c.startswith("effect_views.") for c in _calls(value))


def derived_fields() -> Set[str]:
    """Template fields with a FieldDerivation (card scope)."""
    from engine import effect_views
    return {k for k in effect_views.DERIVATIONS
            if "." not in k and "[" not in k}


def covered(field: str) -> bool:
    from engine import effect_views as v
    return field in v.NON_EFFECT_FIELDS or any(
        k == field or k.startswith(field + "[") for k in v.DERIVATIONS)


def count_a(src: str, derived: Iterable[str]) -> List[str]:
    derived = set(derived)
    return [f"engine/card_database.py:{line} template.{field}"
            for line, field, value in _template_assignments(src)
            if field in derived and not _is_view_call(value)]


def parser_functions(src: str) -> Set[str]:
    return {n.name for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("parse_")}


def count_e(db_src: str, parsers: Iterable[str],
            covered_fn=covered) -> List[str]:
    parsers = set(parsers)
    out = []
    for line, field, value in _template_assignments(db_src):
        used = _calls(value) & parsers
        if used and not covered_fn(field):
            out.append(f"engine/card_database.py:{line} template.{field} = "
                       f"{sorted(used)[0]}(...)")
    return out


# ── (b): runtime oracle reads in resolution handlers ──────────────────

# `text` is a LoyaltyAbility / ActivatedAbility's printed oracle span
# (planeswalker_manager resolves loyalty abilities by reading it).
# `_effective_oracle_text` is the text of the face a permanent shows
# (CardInstance, CR 712.8e): a method whose value is oracle text.
_READ_ATTRS = frozenset({"oracle_text", "oracle", "description",
                         "back_face_oracle", "text",
                         "_effective_oracle_text"})
_READ_PARAMS = frozenset({"oracle", "oracle_text", "oracle_lower",
                          "oracle_l", "desc", "description"})
_SUBSTR = frozenset({"count", "find", "index", "rfind", "rindex",
                     "startswith", "endswith"})
_RE_FUNCS = frozenset({"search", "findall", "match", "fullmatch", "sub",
                       "subn", "finditer", "split"})


def _is_text(node: ast.AST, tainted: Set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Attribute) and node.attr in _READ_ATTRS:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _READ_ATTRS:
            return True
        return _is_text(node.func.value, tainted)
    if isinstance(node, ast.BoolOp):
        return any(_is_text(v, tainted) for v in node.values)
    if isinstance(node, ast.Subscript):
        return _is_text(node.value, tainted)
    if isinstance(node, ast.IfExp):
        return _is_text(node.body, tainted) or _is_text(node.orelse, tainted)
    return False


def _scope_nodes(scope: ast.AST):
    stack = list(getattr(scope, "body", []))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _tainted(scope: ast.AST) -> Set[str]:
    out: Set[str] = set()
    args = getattr(scope, "args", None)
    if isinstance(args, ast.arguments):
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            if a.arg in _READ_PARAMS:
                out.add(a.arg)
    assigns = []
    for n in _scope_nodes(scope):
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.For)):
            value = getattr(n, "value", None) or getattr(n, "iter", None)
            targets = (n.targets if isinstance(n, ast.Assign) else
                       [n.target])
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if value is not None and names:
                assigns.append((names, value))
    changed = True
    while changed:
        changed = False
        for names, value in assigns:
            if all(x in out for x in names):
                continue
            reads = any(isinstance(x, ast.Attribute) and x.attr in _READ_ATTRS
                        for x in ast.walk(value))
            if reads or {x.id for x in ast.walk(value)
                         if isinstance(x, ast.Name)} & out:
                out.update(names)
                changed = True
    return out


def count_b_source(src: str, rel: str, parsers: Iterable[str]
                   ) -> List[str]:
    """Every runtime oracle read in one module's source."""
    tree = ast.parse(src)
    parsers = set(parsers)
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef,
                                         ast.AsyncFunctionDef))]
    hits: Set[Tuple[int, str]] = set()
    for scope in scopes:
        tainted = _tainted(scope)
        for n in _scope_nodes(scope):
            if isinstance(n, ast.Compare):
                for op, comp in zip(n.ops, n.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and \
                            _is_text(comp, tainted):
                        hits.add((n.lineno, "membership test on oracle text"))
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Attribute):
                if f.attr in _SUBSTR and _is_text(f.value, tainted):
                    hits.add((n.lineno, f"oracle.{f.attr}(...)"))
                elif f.attr in _RE_FUNCS and isinstance(f.value, ast.Name) \
                        and f.value.id == "re" and \
                        any(_is_text(a, tainted) for a in n.args):
                    hits.add((n.lineno, f"re.{f.attr}(..., oracle)"))
            name = f.id if isinstance(f, ast.Name) else \
                f.attr if isinstance(f, ast.Attribute) else ""
            if name in parsers:
                hits.add((n.lineno, f"{name}(...) at resolution"))
            elif name == "host_for_override":
                hits.add((n.lineno, "host_for_override(...)"))
    return [f"{rel}:{line} {what}" for line, what in sorted(hits)]


def count_b(parsers: Iterable[str]) -> List[str]:
    out = []
    for rel in HANDLER_MODULES:
        p = ROOT / rel
        if p.is_file():
            out += count_b_source(p.read_text(), rel, parsers)
    return out


# ── (c), (d), (f) ─────────────────────────────────────────────────────

def count_c() -> List[str]:
    from engine import effect_views
    return [f"engine/effect_views.py {n}"
            for n in effect_views.LEGACY_PREDICATES]


def count_d() -> List[str]:
    from engine import effect_resolver
    return [f"{fam}: {code}" for fam, codes in sorted(
        effect_resolver.LEGACY_RESIDUE_TOLERATED.items())
        for code in sorted(codes)]


def count_f(db=None) -> List[str]:
    sys.path.insert(0, str(ROOT / "tools"))
    import effect_spec_equivalence as eq
    if db is None:
        db = eq.load_db()
    templates = eq.pool_templates(db)
    pairs = eq.closure(templates, eq.parse_effects_of(templates))
    return [f"{p.handler} {p.card} {p.host}" for p in pairs
            if not p.new_path]


def counts(pool: bool = False, db=None) -> Dict[str, List[str]]:
    """Every counted site, by ratchet letter."""
    db_src = CARD_DATABASE.read_text()
    parsers = parser_functions(ORACLE_PARSER.read_text())
    out = {"a": count_a(db_src, derived_fields()),
           "b": count_b(parsers),
           "c": count_c(),
           "d": count_d(),
           "e": count_e(db_src, parsers)}
    if pool:
        out["f"] = count_f(db)
    return out


def check(found: Dict[str, List[str]], baseline: Dict[str, int]
          ) -> List[str]:
    """Regressions and stale entries; (e) must be 0."""
    out = []
    for k in COUNTS:
        if k not in found:
            continue
        n, allowed = len(found[k]), int(baseline.get(k, 0))
        if k == "e" and n:
            out.append(f"({k}) {LABELS[k]}: {n} -- every parse_* result "
                       f"must land in a DERIVATIONS or NON_EFFECT_FIELDS "
                       f"field (engine/effect_views.py)")
        elif n > allowed:
            out.append(f"({k}) {LABELS[k]}: {n} > baseline {allowed} "
                       f"(regression)")
        elif n < allowed:
            out.append(f"({k}) {LABELS[k]}: {n} < baseline {allowed} -- "
                       f"stale: lower it in this commit (--update)")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    pool = "--pool" in args
    found = counts(pool=pool)
    if "--list" in args:
        for k in COUNTS:
            if k in found:
                print(f"({k}) {LABELS[k]}: {len(found[k])}")
                for site in found[k]:
                    print(f"    {site}")
        return 0
    if "--update" in args:
        base = json.loads(BASELINE_PATH.read_text()) \
            if BASELINE_PATH.exists() else {}
        base.update({k: len(v) for k, v in found.items()})
        base["description"] = (
            "Effect-parser ratchet (design doc 2026-09-29 section 13): "
            "counts (a)-(f) may only fall; (e) is pinned at 0. See "
            "tools/check_effect_parsers.py.")
        BASELINE_PATH.write_text(json.dumps(base, indent=2, sort_keys=True)
                                 + "\n")
        print(f"wrote {BASELINE_PATH}: " + ", ".join(
            f"{k}={len(v)}" for k, v in found.items()))
        return 0
    baseline = json.loads(BASELINE_PATH.read_text())
    problems = check(found, baseline)
    if problems:
        print("Effect-parser ratchet FAILED:")
        for p in problems:
            print(f"  {p}")
        print("To see every site: python tools/check_effect_parsers.py "
              "--list" + (" --pool" if pool else ""))
        return 1
    print("Effect-parser ratchet OK -- " + ", ".join(
        f"{k}={len(v)}" for k, v in found.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
