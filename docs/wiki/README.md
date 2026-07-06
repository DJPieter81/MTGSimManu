---
title: "Wiki: staging area — how to publish"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - meta
summary: |
  Explains why the GitHub wiki pages are staged here instead of on the
  wiki itself, and the exact steps to publish them. Mark this doc
  archived once the wiki is live.
---

# docs/wiki/ — GitHub wiki staging area

The eight pages in this directory are authored for the **GitHub wiki** of `DJPieter81/MTGSimManu`, not for in-repo reading. They live here because the wiki's git endpoint (`github.com/DJPieter81/MTGSimManu.wiki.git`) was not reachable from the authoring session: the session's authenticated git proxy only authorizes the main repository path, and a GitHub wiki repo additionally does not exist at all until the first page has been created once via the web UI.

## Pages

| File | Wiki page |
|------|-----------|
| `Home.md` | Landing page: overview, quickstart, live links, index |
| `Architecture.md` | Three layers + decision pipeline |
| `Abstraction-Contract.md` | Four questions, prohibitions, ratchet tools |
| `Data-Pipeline.md` | DB parts, provenance, part9 incident |
| `Products.md` | Dashboard, showcase, guides, replay viewer |
| `Calibration-Methodology.md` | Bands, Bo3 canon, seeds |
| `Protocols.md` | Loop-break, test-first, frontmatter registry, WR anchors |
| `Session-Log-2026-07-05.md` | Condensed overhaul session record |

Cross-page links use extension-less wiki form (`[Architecture](Architecture)`) — they resolve on the wiki, not in the repo file browser.

## How to publish

1. **Bootstrap the wiki once via the web UI** — on GitHub: repo → Wiki tab → "Create the first page" → save anything (e.g. a one-line Home). This makes the `.wiki.git` endpoint exist.
2. **Clone and copy** (from any environment with normal GitHub credentials):

   ```bash
   git clone https://github.com/DJPieter81/MTGSimManu.wiki.git
   cd MTGSimManu.wiki
   # copy the pages, stripping the repo-only YAML frontmatter block:
   for f in ../MTGSimManu/docs/wiki/*.md; do
     b=$(basename "$f"); [ "$b" = README.md ] && continue
     awk 'NR==1 && /^---$/ {skip=1; next} skip && /^---$/ {skip=0; next} !skip' "$f" > "$b"
   done
   git add -A
   git commit -m "docs: initial wiki — 8 pages from docs/wiki/ staging"
   git push origin master   # wikis default to master
   ```

3. **After publishing:** mark this README and the eight pages `status: archived` in their frontmatter (or delete the directory in a follow-up PR and let the wiki be the single source), and add the wiki link to the repo README.

The YAML frontmatter on each page exists to satisfy the in-repo doc registry convention (see CLAUDE.md "Session Priorities"); GitHub wikis render frontmatter as literal text, which is why step 2 strips it.
