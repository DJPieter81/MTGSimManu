#!/bin/bash
# overnight.sh — Launch Claude Code with the iterate task
# Usage: claude "$(cat overnight_prompt.txt)"
# Or:    claude < overnight_prompt.txt

cd "$(dirname "$0")"
git pull origin main

echo "=== Starting overnight iterate loop ==="
echo "See OVERNIGHT_TASK.md for full instructions"
echo "Results tracked in iterate_log.json"
echo ""

# Run the loop
for i in $(seq 1 20); do
    echo ""
    echo "╔══════════════════════════════════════╗"
    echo "║  ITERATION $i / 20                    ║"
    echo "╚══════════════════════════════════════╝"
    echo ""
    
    # Step 1: Check current state
    python3 iterate.py --check --n 5 2>/dev/null
    
    # The actual fix step needs Claude Code's intelligence.
    # This script just runs the check loop.
    # For the full AI-driven loop, use Claude Code with overnight_prompt.txt
    
    echo ""
    echo "Iteration $i complete. Check iterate_log.json for results."
    echo "To make fixes, run Claude Code with: claude"
    break  # Remove this line for full loop
done
