"""Ratchet: oracle text must be parsed at load time, not inspected at decision
time in scoring / AI / bookkeeping code.

The rule: every card property derivable from oracle text belongs in a typed
field on CardTemplate, populated once by CardDatabase at load time.  Decision
code reads the field; it must not re-parse the raw oracle string at runtime.

Violations caught (data-flow, AST-based — NOT variable-name-whitelist based,
which is what the old regex detector was; it reported 0 while ~238 real runtime
oracle inspections existed, a false negative that read as a clean surface). A
local bound to oracle text under ANY name and then substring/regex-tested is a
violation regardless of what it is called:
  - `'substring' in X` / `'substring' not in X` where X is oracle text
  - `X.count(...)` / `.find` / `.index` / `.startswith` / `.endswith` on oracle
  - `re.search/findall/match/fullmatch/sub/subn/finditer/split(..., X)`
  - the same tests applied directly to `<obj>.oracle_text` / `<obj>.oracle`

"X is oracle text" means: X is a local/parameter tainted by an assignment whose
right-hand side reads `.oracle_text` / `.oracle` (directly, or through
`.lower()`/`.strip()`/`or ''`/subscript chains, or from another tainted local),
or a function parameter named oracle/oracle_text/oracle_lower/oracle_l. Taint is
tracked per function scope (module top-level included) with a fixpoint.

Excluded modules fall in two groups:
  * Parse-once modules — the sanctioned places that read raw oracle at LOAD:
    oracle_parser.py, card_database.py, target_solver.py, ai/card_features.py.
  * Resolution-fallback layer — the sanctioned places that read oracle at
    RESOLVE time as the generic fallback for cards without a typed field
    (oracle_resolver.py, triggers.py, spell_resolution.py). This is the
    counterpart of the parse-once layer; the typed-field migration (burn,
    board-sweep, targeted-removal, impulse clusters, …) incrementally MOVES
    shapes out of it. The ratchet polices oracle inspection LEAKING into the
    scoring / decision / bookkeeping code outside these layers.

The count may only SHRINK: a new runtime oracle inspection fails CI (build a
typed CardTemplate field instead), and a reduction (typed-field migration)
lowers the baseline in the same commit — run with --update.

Usage:
    python tools/check_oracle_runtime_parse.py            # check (exit 1 on regression)
    python tools/check_oracle_runtime_parse.py --list     # print every violation
    python tools/check_oracle_runtime_parse.py --update   # rewrite baseline (shrink-only intent)
    python tools/check_oracle_runtime_parse.py --baseline PATH
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent

_EXCLUDED = {
    # Parse-once modules (read raw oracle at load).
    "engine/oracle_parser.py",
    "engine/card_database.py",
    "engine/target_solver.py",
    "ai/card_features.py",
    # Resolution-fallback layer (read oracle at resolve time as the generic
    # fallback; being migrated to typed fields — see the module docstring).
    "engine/oracle_resolver.py",
    "engine/triggers.py",
    "engine/spell_resolution.py",
}

_ORACLE_ATTRS = {"oracle_text", "oracle"}
_ORACLE_PARAMS = {"oracle", "oracle_text", "oracle_lower", "oracle_l"}
_SUBSTR_METHODS = {"count", "find", "index", "rfind", "rindex", "startswith",
                   "endswith"}
_RE_FUNCS = {"search", "findall", "match", "fullmatch", "sub", "subn",
             "finditer", "split"}


def _reads_oracle_attr(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in _ORACLE_ATTRS:
            return True
    return False


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _assign_targets(node: ast.AST) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign) and node.target is not None:
        targets = [node.target]
    elif isinstance(node, ast.NamedExpr):
        targets = [node.target]
    else:
        targets = []
    for t in targets:
        if isinstance(t, ast.Name):
            out.append(t.id)
    return out


def _is_oracle_expr(node: ast.AST, tainted: set[str]) -> bool:
    """A value expression that IS oracle text: reads a .oracle attr, or is a
    tainted name (possibly wrapped in .lower()/.strip()/'or …'/subscript)."""
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Attribute) and node.attr in _ORACLE_ATTRS:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_oracle_expr(node.func.value, tainted)
    if isinstance(node, ast.BoolOp):
        return any(_is_oracle_expr(v, tainted) for v in node.values)
    if isinstance(node, ast.Subscript):
        return _is_oracle_expr(node.value, tainted)
    if isinstance(node, ast.IfExp):
        return (_is_oracle_expr(node.body, tainted)
                or _is_oracle_expr(node.orelse, tainted))
    return False


def _iter_scope_body(func: ast.AST):
    """Yield statements/nodes of this scope, not descending into nested
    function/class/lambda definitions (each is its own taint scope)."""
    stack = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        for child in ast.iter_child_nodes(node):
            stack.append(child)


def _scope_tainted(func: ast.AST) -> set[str]:
    tainted: set[str] = set()
    args = getattr(func, "args", None)
    if isinstance(args, ast.arguments):
        for a in (list(args.posonlyargs) + list(args.args)
                  + list(args.kwonlyargs)):
            if a.arg in _ORACLE_PARAMS:
                tainted.add(a.arg)
    assigns: list[tuple[list[str], ast.AST]] = []
    for node in _iter_scope_body(func):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            val = getattr(node, "value", None)
            if val is not None:
                assigns.append((_assign_targets(node), val))
        for sub in ast.walk(node):
            if isinstance(sub, ast.NamedExpr):
                assigns.append((_assign_targets(sub), sub.value))
    changed = True
    while changed:
        changed = False
        for names, val in assigns:
            if not names or any(n in tainted for n in names):
                continue
            if _reads_oracle_attr(val) or (_names_in(val) & tainted):
                for n in names:
                    tainted.add(n)
                changed = True
    return tainted


def _count_scope(func: ast.AST, tainted: set[str]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []

    def _is_oracle(n):
        return _is_oracle_expr(n, tainted)

    for node in _iter_scope_body(func):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_oracle(comp):
                    hits.append((node.lineno, "membership test on oracle text"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            if f.attr in _SUBSTR_METHODS and _is_oracle(f.value):
                hits.append((node.lineno, f"oracle_text.{f.attr}(...)"))
            elif (f.attr in _RE_FUNCS and isinstance(f.value, ast.Name)
                  and f.value.id == "re"
                  and any(_is_oracle(a) for a in node.args)):
                hits.append((node.lineno, f"re.{f.attr}(..., oracle_text)"))
    return hits


def _count_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, PermissionError, SyntaxError):
        return []
    scopes: list[ast.AST] = [tree]
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(n)
    seen: set[tuple[int, str]] = set()
    hits: list[tuple[int, str]] = []
    for scope in scopes:
        tainted = _scope_tainted(scope)
        for key in _count_scope(scope, tainted):
            if key not in seen:
                seen.add(key)
                hits.append(key)
    hits.sort()
    return hits


def _scan() -> dict[str, list[tuple[int, str]]]:
    violations: dict[str, list[tuple[int, str]]] = {}
    for d in (ROOT / "engine", ROOT / "ai"):
        for path in sorted(d.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in _EXCLUDED:
                continue
            hits = _count_violations(path)
            if hits:
                violations[rel] = hits
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    baseline_path = ROOT / "tools" / "oracle_runtime_parse_baseline.json"
    for i, arg in enumerate(args):
        if arg == "--baseline" and i + 1 < len(args):
            baseline_path = pathlib.Path(args[i + 1])
            break

    violations = _scan()
    total = sum(len(v) for v in violations.values())

    if "--list" in args:
        for rel, hits in sorted(violations.items()):
            for lineno, text in hits:
                print(f"  {rel}:{lineno}  {text}")
        print(f"\nTotal oracle-runtime-parse violations: {total}")
        return 0

    if "--update" in args:
        baseline_path.write_text(json.dumps({
            "total": total,
            "description": ("runtime oracle-text inspections in scoring / AI / "
                            "bookkeeping code (AST data-flow detector; the "
                            "parse-once and resolution-fallback layers are "
                            "excluded — see check_oracle_runtime_parse.py). May "
                            "only shrink: a new inspection fails CI (build a "
                            "typed CardTemplate field), a typed-field migration "
                            "lowers this in the same commit."),
        }, indent=2) + "\n")
        print(f"Wrote {baseline_path} with total = {total}.")
        return 0

    if not baseline_path.exists():
        print("ERROR: tools/oracle_runtime_parse_baseline.json not found.")
        return 1
    with open(baseline_path) as f:
        allowed = int(json.load(f).get("total", 0))

    if total > allowed:
        print(f"Oracle-runtime-parse ratchet FAILED: regression.\n"
              f"{total} runtime oracle inspections, baseline allows {allowed}.\n"
              f"Oracle text must be parsed once at load into a typed CardTemplate "
              f"field and read as that field at decision time — not re-parsed in "
              f"scoring/AI/bookkeeping code.\n"
              f"To see all: python tools/check_oracle_runtime_parse.py --list")
        extra = 0
        for rel, hits in sorted(violations.items()):
            for lineno, text in hits:
                print(f"  {rel}:{lineno}  {text}")
                extra += 1
                if extra >= 10:
                    break
            if extra >= 10:
                break
        return 1
    if total < allowed:
        print(f"Oracle-runtime-parse ratchet FAILED: baseline is stale.\n"
              f"{total} remain but baseline claims {allowed}. You removed "
              f"{allowed - total} — claim it: run "
              f"`python tools/check_oracle_runtime_parse.py --update` in the "
              f"same commit.")
        return 1
    print(f"Oracle-runtime-parse ratchet OK — total = {total} (baseline = {allowed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
