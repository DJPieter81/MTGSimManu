#!/usr/bin/env bash
# One-time setup: point git at the versioned hooks directory.
# Run from repo root: bash tools/install_hooks.sh
set -e
git config core.hooksPath .git-hooks
chmod +x .git-hooks/pre-commit
echo "Installed: core.hooksPath -> .git-hooks"
echo "Verify with: git config --get core.hooksPath"
