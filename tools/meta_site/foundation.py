"""The rules foundation, read from the tree at build time (no card database).

build_data.py stores what this returns under `content.foundation`, so every
figure the showcase prints about the engine's guard rails is the tree's own:

  * `ratchet_board`  -- each CI ratchet's current count, read from its
    baseline file under tools/, with the direction the ratchet allows;
  * `proofs`         -- the equivalence harness record, gate parity, the
    clause-grammar census and the seeded-game digest, from their files;
  * `audit_rules`    -- every CR-phrased rule id the engine's auditor can
    record (string literals "<CR>/<slug>" in engine/), split into
    invariants and census rows, counted by CR chapter;
  * `ledger`         -- every unit the rules-foundation tracker records under
    a heading "## Unit <code>: <rule> (<CR cites>; <date>)";
  * `module_lines`   -- the line count of each file a layer lists.

Every reader returns an empty value when its file is absent, so a fixture
repository builds without them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# (baseline file, key, label, what it counts, reader). The rule is what CI
# allows: "pinned at 0", "may only fall", "must equal the count".
_FALL, _ZERO, _EXACT = "may only fall", "pinned at 0", "must equal the count"


def _sum_ints(d: dict) -> int:
    return sum(v for k, v in d.items()
               if isinstance(v, int) and not isinstance(v, bool)
               and not k.startswith("_"))


RATCHETS = (
    ("abstraction_baseline.json", "names", "Card-name conditionals",
     "`card.name == ...` gates in engine/ and ai/",
     lambda d: d.get("hardcoded_name_count")),
    ("abstraction_baseline.json", "deck_gates", "Deck-name gates",
     "branches on a deck's name in engine/ and ai/",
     lambda d: d.get("deck_gate_count")),
    ("card_name_registry_baseline.json", "registry", "Name-keyed effect handlers",
     "EFFECT_REGISTRY entries registered under a card's name",
     lambda d: d.get("total")),
    ("magic_numbers_baseline.json", "magic_numbers", "Bare numbers in the AI",
     "numeric literals in ai/*.py outside its infrastructure modules",
     _sum_ints),
    ("single_owner_baseline.json", "single_owner", "Second rule paths",
     "target picks, damage or life writes and counter writes outside "
     "their owner", _sum_ints),
    ("zone_mutation_baseline.json", "zone_mutation", "Raw zone writes",
     "`card.zone = ...` outside the zone-transfer funnel", _sum_ints),
    ("oracle_runtime_parse_baseline.json", "oracle_runtime", "Runtime oracle reads",
     "oracle-text inspections in scoring, AI and bookkeeping code",
     lambda d: d.get("total")),
    ("effect_parsers_baseline.json", "effect_parsers", "Legacy effect parsers",
     "per-shape parsers, runtime oracle reads in resolution handlers and "
     "legacy quirk predicates the grammar has not retired",
     lambda d: sum(d.get(k, 0) for k in "abcde")),
    ("temporal_state_baseline.json", "temporal", "Temporal-state bypasses",
     "rule-effect state read around its owner, or reset off the turn clock",
     _sum_ints),
    ("narrow_typed_fields_baseline.json", "narrow_fields", "Narrow typed fields",
     "typed card fields that only one or two cards populate (declared, "
     "with reasons)", lambda d: len(d.get("fields") or {})),
    ("loyalty_dispatch_baseline.json", "loyalty", "Unexecutable loyalty lines",
     "printed loyalty abilities the engine refuses before loyalty is paid",
     lambda d: d.get("unclassified")),
)
_RULES = {"names": _ZERO, "deck_gates": _ZERO, "loyalty": _EXACT}


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def ratchet_board(root: Path) -> List[Dict[str, Any]]:
    rows = []
    for fname, key, label, counts, read in RATCHETS:
        d = _load(Path(root) / "tools" / fname)
        if d is None:
            continue
        now = read(d)
        if now is None:
            continue
        rows.append({"key": key, "label": label, "counts": counts, "now": int(now),
                     "rule": _RULES.get(key, _FALL),
                     "file": f"tools/{fname}"})
    return rows


def proofs(root: Path) -> Dict[str, Any]:
    tools = Path(root) / "tools"
    out: Dict[str, Any] = {}
    rec = _load(tools / "host_harness_switched.json")
    if rec is not None:
        pairs = rec.get("pairs") or []
        intended = rec.get("intended") or {}
        out["harness"] = {"identical": len(pairs), "intended": len(intended),
                          "boards": rec.get("boards"), "seeds": rec.get("seeds")}
    eq = _load(tools / "effect_spec_equivalence_baseline.json")
    if eq is not None:
        gp = eq.get("gate_parity") or {}
        totals = eq.get("totals") or {}
        out["gate_parity"] = {"pairs": gp.get("pairs"),
                              "new_path": gp.get("new_path"),
                              "legacy_fallback": gp.get("legacy_fallback")}
        out["equivalence"] = {"fields": len(eq.get("fields") or {}),
                              "agree": totals.get("AGREE"),
                              "unexplained": totals.get("UNEXPLAINED"),
                              "semantic_fix": totals.get("SEMANTIC_FIX")}
    census = _load(tools / "effect_census_baseline.json")
    if census is not None:
        r = census.get("report") or {}
        specs, typed = r.get("specs") or 0, r.get("typed") or 0
        deck = (r.get("deck") or {})
        out["census"] = {"cards": r.get("cards"), "specs": specs, "typed": typed,
                         "typed_share": round(typed / specs, 4) if specs else None,
                         "deck_typed_share": (census.get("pinned") or {}).get("deck_typed_share"),
                         "by_host_kind": {k: v.get("share") for k, v in
                                          (r.get("by_host_kind") or {}).items()},
                         "deck_cards": deck.get("cards")}
    dg = _load(tools / "seeded_game_digest_baseline.json")
    if dg is not None:
        out["digest"] = {"games": dg.get("n_games") or len(dg.get("games") or {})}
    return out


_RULE_ID = re.compile(r'"([0-9]{3}(?:\.[0-9]+[a-z]*)?/[a-z0-9_]+)"')
_CENSUS_CALL = re.compile(r'census\(\s*"([^"]+)"')
CR_CHAPTERS = {"1": "Game concepts", "2": "Parts of a card", "3": "Card types",
               "4": "Zones", "5": "Turn structure",
               "6": "Spells, abilities and effects", "7": "Additional rules"}


def audit_rules(root: Path) -> Dict[str, Any]:
    """The rule ids the auditor records, from the engine's source: an id
    passed to `census(...)` or ending in `_unresolved` is a census row (a
    fact counted, not a violation); every other is an invariant."""
    engine = Path(root) / "engine"
    if not engine.exists():
        return {}
    ids, census = set(), set()
    for f in sorted(engine.rglob("*.py")):
        text = f.read_text(errors="ignore")
        ids.update(_RULE_ID.findall(text))
        census.update(_CENSUS_CALL.findall(text))
    census |= {i for i in ids if i.endswith("_unresolved")}
    invariants = sorted(ids - census)
    chapters: Dict[str, int] = {}
    for i in invariants:
        chapters[i[0]] = chapters.get(i[0], 0) + 1
    return {"invariants": invariants, "census": sorted(ids & census),
            "by_chapter": [{"chapter": k, "name": CR_CHAPTERS.get(k, k),
                            "count": v} for k, v in sorted(chapters.items())]}


_UNIT = re.compile(r"^## Units? ([A-Z0-9][A-Za-z0-9.+\- ]*?)(?::| —) (.+)$")
_DATE = re.compile(r"(20\d\d-\d\d-\d\d)")
_CR = re.compile(r"CR ([0-9][0-9a-z.,\s\-]*[0-9a-z])")


def ledger(root: Path) -> List[Dict[str, Any]]:
    """Every unit heading of the tracker: code, rule, CR cites, date. The
    rule is the heading's sentence; its last parenthesis holds the cites
    and the date."""
    path = Path(root) / "docs" / "design" / "rules-foundation-sweep-tracker.md"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = _UNIT.match(line)
        if not m:
            continue
        code, rest = m.group(1).strip(), m.group(2).strip()
        meta, rule = "", rest
        if rest.endswith(")") and "(" in rest:
            depth, i = 0, len(rest) - 1
            while i >= 0:
                depth += rest[i] == ")"
                depth -= rest[i] == "("
                if depth == 0:
                    break
                i -= 1
            meta, rule = rest[i + 1:-1], rest[:i].rstrip()
        date = _DATE.search(meta)
        if not date:
            continue
        cites = []
        for c in _CR.findall(meta):
            cites += [x.strip() for x in c.split(",") if x.strip()]
        out.append({"code": code, "rule": rule, "cr": cites,
                    "date": date.group(1),
                    "ai": bool(re.search(r"\bAI\b", meta))})
    return out


def _first_sentence(doc: str) -> str:
    doc = " ".join(doc.split())
    for i, ch in enumerate(doc):
        if ch in ".?" and (i + 1 == len(doc) or doc[i + 1] == " ") and i > 10:
            return doc[:i + 1]
    return doc


def _methods(tree: Any, cls: str) -> Dict[str, Any]:
    import ast
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {fn.name: fn for fn in node.body
                    if isinstance(fn, ast.FunctionDef)
                    and not fn.name.startswith("_")}
    return {}


# How AICallbacks reaches the deciding seat's own AI player.
_SEATS = ("player_ai", "_players")


def choice_points(root: Path) -> List[Dict[str, Any]]:
    """The choices the engine asks of a player (`engine/callbacks.py`,
    protocol `GameCallbacks`, each with its docstring's first sentence) and
    who answers each for the AI (`engine/game_runner.AICallbacks`): the
    `ai.*` functions its method imports, the deciding player's own AI when
    it asks `player_ai`, else the engine's default answer from printed
    data."""
    import ast
    cb, gr = Path(root) / "engine" / "callbacks.py", Path(root) / "engine" / "game_runner.py"
    try:
        protocol = _methods(ast.parse(cb.read_text()), "GameCallbacks")
        runner = ast.parse(gr.read_text())
        answers = _methods(runner, "AICallbacks")
    except (OSError, SyntaxError):
        return []
    # The runner's module-level `from ai.<m> import <f>` names, so a call
    # of one inside a method is attributed to its AI module.
    top = {a.asname or a.name: f"{n.module}.{a.name}" for n in runner.body
           if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("ai.")
           for a in n.names}
    rows = []
    for name, fn in protocol.items():
        mine = answers.get(name)
        funcs, player, default = [], False, False
        if mine is not None:
            for n in ast.walk(mine):
                if isinstance(n, ast.ImportFrom):
                    if (n.module or "").startswith("ai."):
                        funcs += [f"{n.module}.{a.name}" for a in n.names]
                    elif any(a.name.startswith("default_") for a in n.names):
                        default = True
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id in top:
                    funcs.append(top[n.func.id])
                if (isinstance(n, ast.Attribute) and n.attr in _SEATS) or (
                        isinstance(n, ast.Constant) and n.value in _SEATS):
                    player = True
        # Functions only: a data class it builds (an Action, a Choice) is
        # not who answers.
        funcs = [f for f in funcs if not f.rsplit(".", 1)[-1][:1].isupper()]
        by = ("player" if player else "ai" if funcs else
              "default" if default or mine is None else "callbacks")
        rows.append({"method": name,
                     "asks": _first_sentence(ast.get_docstring(fn) or ""),
                     "by": by, "answers": sorted(set(funcs))})
    return rows


def module_lines(root: Path, paths: List[str]) -> Dict[str, int]:
    out = {}
    for p in paths:
        f = Path(root) / p
        if f.is_file():
            out[p] = len(f.read_text(errors="ignore").splitlines())
    return out


def foundation(root: Path, layer_paths: List[str]) -> Dict[str, Any]:
    return {"ratchets": ratchet_board(root), "proofs": proofs(root),
            "audit": audit_rules(root), "ledger": ledger(root),
            "choices": choice_points(root),
            "lines": module_lines(root, layer_paths)}
