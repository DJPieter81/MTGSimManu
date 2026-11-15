"""Build the metagame dashboard (modern_meta_matrix_full.html).

Thin wrapper over tools/meta_site, the one pipeline that builds both the
dashboard and the showcase from data/meta_site.json:

    python3 build_dashboard.py --merge   # results -> data/meta_site.json -> pages
    python3 build_dashboard.py           # re-render pages from data/meta_site.json

`merge()` is what `run_meta.py --matrix --save` calls. Every output path is
inside the repository this file lives in.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.meta_site import build_data, render  # noqa: E402
from tools.meta_site.schema import SiteData  # noqa: E402


def load_D(jsx_path='metagame_data.jsx'):
    """The D object from metagame_data.jsx (kept for its readers)."""
    import json
    text = Path(jsx_path).read_text()
    start = text.index('const D = ') + len('const D = ')
    return json.loads(text[start:text.index(';\nconst N')])


def merge(results_path='metagame_results.json', root=ROOT):
    """Rebuild data/meta_site.json from a matrix results file, then render
    every page. Non-matrix results are skipped."""
    import json
    results = json.loads(Path(root, results_path).read_text())
    if results.get('type') != 'matrix':
        print('merge: skipping, the results file is not a matrix run', file=sys.stderr)
        return []
    current = Path(root, 'data', 'meta_site.json')
    if current.exists():
        have = {d.name for d in SiteData.model_validate_json(current.read_text()).decks}
        missing = sorted(have - set(results.get('names') or []))
        if missing:
            print(f"merge: skipping, the results file is missing {len(missing)} deck(s) "
                  f"already on the site ({', '.join(missing[:5])}); run the full matrix",
                  file=sys.stderr)
            return []
    data = build_data.build_site_data(Path(root), results_path=Path(root, results_path))
    build_data.write_site_data(data, Path(root))
    written = render.render_all(data, Path(root))
    print(f"merge: {len(data.decks)} decks, n={data.provenance.n_per_pair}; "
          f"wrote {', '.join(str(Path(p).relative_to(root)) for p in written)}")
    return written


def build(root=ROOT):
    """Re-render every page from the committed data/meta_site.json."""
    data = SiteData.model_validate_json(Path(root, 'data', 'meta_site.json').read_text())
    return render.render_all(data, Path(root))


if __name__ == '__main__':
    merge() if '--merge' in sys.argv else build()
