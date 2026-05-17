"""One-shot sweep: remove module-scoped `card_db` fixtures from test files.

Each removed fixture is a 3-7 line block of the form:

    @pytest.fixture(scope="module")
    def card_db() -> CardDatabase:
        return CardDatabase()

These all duplicate the session-scoped fixture in tests/conftest.py.
The local versions force a full 21k-card DB reload per test module,
making the full pytest suite infeasible in CI (~80 min). Deleting them
falls through to the session fixture and brings the suite to ~5 min.

Pre-conditions verified before the sweep:
  - All 150 fixture bodies are functionally `return CardDatabase()`.
  - No tests mutate `card_db.cards` or `card_db.templates`.
  - No fixtures take parameters or have non-default scopes.

Run once; not committed to be re-run.
"""
import ast
import glob
import os
import sys


def remove_card_db(src: str) -> str | None:
    """Return modified source with card_db fixture removed.
    Returns None if no fixture found."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'card_db':
            start = min((d.lineno for d in node.decorator_list), default=node.lineno)
            end = node.end_lineno
            lines = src.splitlines(keepends=True)
            new_lines = lines[:start - 1] + lines[end:]
            # Collapse 3+ consecutive blank lines to 2.
            collapsed = []
            blank_run = 0
            for ln in new_lines:
                if ln.strip() == '':
                    blank_run += 1
                    if blank_run <= 2:
                        collapsed.append(ln)
                else:
                    blank_run = 0
                    collapsed.append(ln)
            return ''.join(collapsed)
    return None


def main():
    root = os.path.join(os.path.dirname(__file__), '..', 'tests')
    root = os.path.abspath(root)
    files = sorted(glob.glob(os.path.join(root, '*.py')))
    changed = 0
    for f in files:
        if os.path.basename(f) == 'conftest.py':
            continue  # session-scoped fixture must remain
        with open(f) as fp:
            src = fp.read()
        new_src = remove_card_db(src)
        if new_src is not None and new_src != src:
            with open(f, 'w') as fp:
                fp.write(new_src)
            changed += 1
    print(f"Removed card_db fixture from {changed} files.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
