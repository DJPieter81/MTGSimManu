#!/bin/bash
# overnight_loop.sh — Sustained iterate loop via repeated Claude Code launches
# Usage: cd ~/MTGSimManu && ./overnight_loop.sh
#
# Each launch does 1-3 fixes in ~10-15 min, then exits.
# This script relaunches it up to 30 times (~5-6 hours total).
# Progress tracked in iterate_log.json and git log.

set -e
cd "$(dirname "$0")"

MAX_LAUNCHES=30
LOG="overnight_run.log"
START=$(date +%s)

echo "=== MTGSimManu Overnight Loop ===" | tee -a "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo "Max launches: $MAX_LAUNCHES" | tee -a "$LOG"
echo "" | tee -a "$LOG"

git pull origin main

for i in $(seq 1 $MAX_LAUNCHES); do
    ELAPSED=$(( ($(date +%s) - START) / 60 ))
    
    # Stop after 6 hours
    if [ $ELAPSED -gt 360 ]; then
        echo "[$i] 6 hours elapsed. Stopping." | tee -a "$LOG"
        break
    fi
    
    echo "╔══════════════════════════════════════════════╗" | tee -a "$LOG"
    echo "║  LAUNCH $i / $MAX_LAUNCHES  (${ELAPSED}m elapsed)              ║" | tee -a "$LOG"
    echo "╚══════════════════════════════════════════════╝" | tee -a "$LOG"
    
    # Pull latest (previous launch may have pushed)
    git pull --rebase origin main 2>&1 | tee -a "$LOG"
    
    # Count commits before
    BEFORE=$(git rev-list --count HEAD)
    
    # Launch Claude Code — single shot, exits when done
    claude --dangerously-skip-permissions -p "$(cat overnight_prompt.txt)

IMPORTANT CONTEXT: This is launch $i of $MAX_LAUNCHES in an automated loop. You will be relaunched after you exit. Focus on making 1-2 solid iterate fixes this launch. Do not try to do everything — just pick the worst outlier you can fix, fix it, test it, commit it. Then you can exit and the next launch will continue.

Previous launches have already run. Check git log --oneline -5 to see what was already fixed. Do NOT redo work. Check iterate_log.json for history.

Remember: ONLY iterate fixes. No guides, no HTML, no replays, no refactors. If you touch a forbidden file, you are wasting a launch." 2>&1 | tee -a "$LOG"
    
    # Count commits after
    git pull --rebase origin main 2>/dev/null
    AFTER=$(git rev-list --count HEAD)
    NEW_COMMITS=$((AFTER - BEFORE))
    
    echo "" | tee -a "$LOG"
    echo "[$i] Finished. New commits: $NEW_COMMITS" | tee -a "$LOG"
    echo "[$i] Total elapsed: ${ELAPSED}m" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    
    # Brief pause between launches
    sleep 10
done

TOTAL_ELAPSED=$(( ($(date +%s) - START) / 60 ))
echo "=== Loop complete ===" | tee -a "$LOG"
echo "Total time: ${TOTAL_ELAPSED}m" | tee -a "$LOG"
echo "Check: git log --oneline -20" | tee -a "$LOG"
