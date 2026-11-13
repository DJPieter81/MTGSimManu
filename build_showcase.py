"""Build the project showcase (templates/reference_showcase.html and the
root mtgsimmanu_showcase.html).

Thin wrapper over tools/meta_site: the showcase and the dashboard are
rendered together from data/meta_site.json, so this re-renders both.
Narrative content (timeline, architecture, roadmap) is data under
tools/meta_site/content/.

    python3 build_showcase.py
"""
from __future__ import annotations

import build_dashboard

if __name__ == '__main__':
    build_dashboard.build()
