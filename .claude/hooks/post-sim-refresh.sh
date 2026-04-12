#!/bin/bash
# Post-simulation hook for MTGSimManu (Modern).
# Fires on PostToolUse for Bash — filters to sim-related commands only.
# When triggered, injects a systemMessage telling Claude to auto-refresh dashboards + replays.
set -e

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
EXIT_CODE=$(echo "$INPUT" | jq -r '.tool_output.exit_code // 0')

# Only trigger on commands that succeeded
if [[ "$EXIT_CODE" != "0" ]]; then
  exit 0
fi

# Pattern match sim commands (Modern repo patterns)
IS_SIM=false
if [[ "$COMMAND" =~ run_meta\.py ]] && [[ ! "$COMMAND" =~ --list ]]; then
  # Any run_meta.py call except --list (which just prints deck names)
  IS_SIM=true
elif [[ "$COMMAND" =~ metagame_fast\.py ]]; then
  IS_SIM=true
elif [[ "$COMMAND" =~ run_meta_matrix ]]; then
  IS_SIM=true
fi

if [[ "$IS_SIM" != "true" ]]; then
  exit 0
fi

# Find the most recent results JSON
CWD=$(echo "$INPUT" | jq -r '.cwd')
LATEST_NAME="unknown"
for DIR in "$CWD/results" "$CWD/meta" "$CWD"; do
  if [[ -d "$DIR" ]]; then
    FOUND=$(ls -t "$DIR"/*.json 2>/dev/null | head -1)
    if [[ -n "$FOUND" ]]; then
      LATEST_NAME=$(basename "$FOUND")
      break
    fi
  fi
done

# Return system message telling Claude to refresh deliverables
cat <<EOF
{
  "continue": true,
  "suppressOutput": false,
  "systemMessage": "SIMULATION COMPLETE — AUTO-REFRESH TRIGGERED. Latest results: ${LATEST_NAME}. Now do the following:\n\n1. DASHBOARD: Use the /mtg-meta-matrix skill to regenerate the interactive React metagame heatmap from the latest results JSON. Save the .jsx to the user output folder.\n\n2. OUTLIER DETECTION: Identify up to 3 matchups where WR deviates >10pp from expected (known Tier 1 below 45%, or individual matchup >75% or <25% that seems wrong).\n\n3. REPLAYS: For each outlier, use /mtg-bo3-replayer-v2 to generate a debug HTML replay (seeds 80000+). Try to find a game where the result contradicts the overall trend. Save to output folder.\n\n4. SUMMARY: Report results file used, dashboard path, outliers with reasoning, replay files with seeds, and any engine limitations observed.\n\nPresent all files to the user when done."
}
EOF
