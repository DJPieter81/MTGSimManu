"""One-off migration: route every test onto the single shared CardDatabase.

Three transforms, applied per file under tests/:

1. Trivial fixture named ``card_db`` (decorated ``@pytest.fixture(...)`` whose
   body is only a docstring/import + ``return CardDatabase()``) is deleted, so
   the file inherits the session-scoped ``card_db`` fixture from conftest.py.

2. Trivial fixture with any other name (e.g. ``db``) is rewritten to delegate:
   ``def NAME(card_db): return card_db`` — preserving the local fixture name so
   call sites are untouched, but reusing the session instance (no reload).

3. Direct ``CardDatabase()`` calls inside non-fixture functions/helpers are
   rewritten to ``shared_card_database()`` (process-wide singleton), and the
   import is added.

Non-trivial fixtures (any setup beyond ``return CardDatabase()``) are left
untouched and reported. Run with --apply to write changes; default is dry-run.
"""

import argparse
import ast
import glob
import os
import re


def _is_cdb_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CardDatabase"
    )


def _is_fixture(fn):
    for d in fn.decorator_list:
        if isinstance(d, ast.Attribute) and d.attr == "fixture":
            return True
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "fixture":
            return True
    return False


def _is_trivial_fixture(fn):
    """Body is only docstring / CardDatabase import + `return CardDatabase()`."""
    body = [
        b
        for b in fn.body
        if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))
        and not isinstance(b, (ast.Import, ast.ImportFrom))
    ]
    return len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value and _is_cdb_call(body[0].value)


def _block_span(fn):
    """1-based inclusive line span covering decorators + def body."""
    start = min(d.lineno for d in fn.decorator_list) if fn.decorator_list else fn.lineno
    return start, fn.end_lineno


def process(path, apply):
    src = open(path).read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    lines = src.splitlines(keepends=True)

    delete_spans = []      # (start, end) to remove entirely
    rewrite_spans = []     # (start, end, new_text)
    nontrivial = []
    need_shared = False

    for fn in tree.body:  # module-level only
        if not isinstance(fn, ast.FunctionDef):
            continue
        if _is_fixture(fn):
            if _is_trivial_fixture(fn):
                start, end = _block_span(fn)
                if fn.name == "card_db":
                    delete_spans.append((start, end))
                else:
                    rewrite_spans.append(
                        (start, end, f"@pytest.fixture\ndef {fn.name}(card_db):\n    return card_db\n")
                    )
            elif any(_is_cdb_call(n) for n in ast.walk(fn)):
                nontrivial.append(fn.name)

    # Direct CardDatabase() calls in non-fixture funcs anywhere in the file.
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and not _is_fixture(fn):
            if any(_is_cdb_call(n) for n in ast.walk(fn)):
                need_shared = True

    if not (delete_spans or rewrite_spans or need_shared):
        return {"nontrivial": nontrivial, "changed": False}

    # Mark fixture-block line ranges so we don't rewrite CardDatabase() inside
    # blocks we're about to delete/replace.
    blocked = set()
    for s, e in delete_spans:
        blocked.update(range(s, e + 1))
    for s, e, _ in rewrite_spans:
        blocked.update(range(s, e + 1))

    # Apply line edits high-to-low to keep indices stable.
    edits = [("del", s, e, None) for s, e in delete_spans] + [
        ("sub", s, e, t) for s, e, t in rewrite_spans
    ]
    edits.sort(key=lambda x: x[1], reverse=True)
    for kind, s, e, text in edits:
        # Also swallow trailing blank lines after a deleted fixture to avoid
        # leaving 3+ blank lines.
        del_end = e
        if kind == "del":
            while del_end < len(lines) and lines[del_end].strip() == "":
                del_end += 1
            lines[s - 1 : del_end] = []
        else:
            lines[s - 1 : e] = [text]

    out = "".join(lines)

    # Rewrite remaining direct CardDatabase() calls -> shared_card_database().
    if need_shared:
        out = re.sub(r"\bCardDatabase\(\)", "shared_card_database()", out)
        if "from tests._card_db_cache import shared_card_database" not in out:
            # Insert import after the engine.card_database import if present,
            # else after the last top-level import line.
            imp = "from tests._card_db_cache import shared_card_database\n"
            m = re.search(r"^from engine\.card_database import .*\n", out, re.M)
            if m:
                out = out[: m.end()] + imp + out[m.end() :]
            else:
                lines2 = out.splitlines(keepends=True)
                last = 0
                for i, ln in enumerate(lines2):
                    if ln.startswith("import ") or ln.startswith("from "):
                        last = i + 1
                lines2.insert(last, imp)
                out = "".join(lines2)

    # Drop now-unused `from engine.card_database import CardDatabase`.
    if "CardDatabase" not in re.sub(
        r"^from engine\.card_database import CardDatabase\n", "", out, flags=re.M
    ):
        out = re.sub(r"^from engine\.card_database import CardDatabase\n", "", out, flags=re.M)

    # Collapse 3+ consecutive blank lines to 2.
    out = re.sub(r"\n{4,}", "\n\n\n", out)

    if apply and out != src:
        with open(path, "w") as f:
            f.write(out)

    return {"nontrivial": nontrivial, "changed": out != src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    changed = 0
    nontrivial_files = []
    for path in sorted(glob.glob("tests/**/*.py", recursive=True)):
        if os.path.basename(path) in ("conftest.py", "_card_db_cache.py"):
            continue
        r = process(path, args.apply)
        if r is None:
            continue
        if r["changed"]:
            changed += 1
        if r["nontrivial"]:
            nontrivial_files.append((path, r["nontrivial"]))
    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {changed} files changed")
    if nontrivial_files:
        print("Non-trivial fixtures left for manual review:")
        for p, names in nontrivial_files:
            print(f"  {p}: {names}")


if __name__ == "__main__":
    main()
