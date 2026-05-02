# Weekly fix-attempt scheduled task

> Cowork won't let a scheduled task create another scheduled task. So this lives
> here as a ready-to-paste SKILL.md. To install: open a fresh Cowork chat and
> ask Claude "create a scheduled task with this prompt: <paste the body
> below>", or use Settings → Scheduled Tasks → Add manually.

**Schedule:** every Friday at 10:00 AM local time (cron: `0 10 * * 5`)
**Task ID:** `mtgsimmanu-weekly-fix-attempt`
**Description:** Pick highest-priority open bug from MTGSimManu P0/P1 backlog and attempt a fix.

## Prompt body to paste

```
You are running the weekly MTGSimManu bug-fix attempt. The project is a Magic: The Gathering Modern-format game simulator with an open backlog of P0/P1 bugs. Your job: pick the highest-priority open bug, write a failing test that names the rule, implement the fix, push to a claude/* branch.

The repo is at /Users/lynette/MTGSimManu/MTGSimManu/MTGSimManu (workspace mount). The Cowork sandbox cannot push directly because of virtiofs unlink limits — work in $HOME/work/MTGSimManu (clone fresh from origin if missing) and push from there.

Read CLAUDE.md and PROJECT_STATUS.md first. The ABSTRACTION CONTRACT is binding: failing test FIRST, no `card.name == "X"` patterns in `engine/` or `ai/`, no magic numbers without inline comment + named-rule test.

## Step 1: discover the priority

cd $HOME/work/MTGSimManu
git fetch origin main
git reset --hard origin/main
grep -rEl '^status: active' docs/ --include='*.md' | xargs grep -l '^priority: primary'

Expect to find docs/proposals/2026-05-03_p0_p1_backlog.md (or its successor). Read it. Find the first bug under "Order of attack" that does NOT already have a claude/fix-<bug-name> branch on origin (check via git branch -r | grep claude/fix-).

## Step 2: do the work

In $HOME/work/MTGSimManu:
1. git checkout -b claude/fix-<bug-name>
2. Read the relevant source files. Don't guess — grep, trace, look at actual call sites.
3. Write a failing test in tests/test_<rule_name>.py. Test name encodes the *rule* (mechanic-phrased), not the card name. Run pytest — it MUST go red.
4. Implement the fix. Generic, oracle-driven, no per-card hardcodes.
5. Run pytest again — it MUST pass. Run python tools/check_abstraction.py — must exit 0.
6. Smoke test: run a single relevant matchup with python run_meta.py --bo3 <decks> -s <seed> and verify the rule fires in-game.
7. git add . && git commit -m "fix(<scope>): <summary>" with body explaining diagnosis, fix, generalization (name another card the same fix helps), test name, smoke result.
8. Push using PAT: git push "https://x-access-token:<PAT>@github.com/DJPieter81/MTGSimManu.git" claude/fix-<name>

If you don't have a PAT, halt and report — don't try to push without one.

## Step 3: open the PR

The sandbox cannot reach api.github.com. Provide the PR-create URL in your final report:
https://github.com/DJPieter81/MTGSimManu/pull/new/claude/fix-<bug-name>

The user opens it in browser.

## Step 4: update the backlog

Edit docs/proposals/<latest>_p0_p1_backlog.md — add a row to the "Per-bug scheduling" table noting the branch and test status. Do NOT mark resolved until the PR merges and a follow-up matrix run shows the WR delta.

Commit + push to a separate branch: git checkout -b claude/backlog-update-<date> and push.

## Constraints

- Do not run a full matrix sim — that needs the user's Mac (Cowork sandbox compute is too slow).
- Do not modify the workspace mount directly — work in $HOME/work and push.
- If pytest can't even reach the test setup (API mismatch, fixture errors), STOP and write a docs/diagnostics/<date>_<bug>_test_blocker.md with the API problem documented. Don't ship broken tests.
- If the fix would require ≥3 file changes spanning unrelated subsystems, STOP and write a docs/proposals/<date>_<bug>_design.md rather than power through.

## Final report

Under 300 words: which bug, branch name, commit hash, test name, smoke outcome, PR URL, any blockers. Note PAT rotation reminder if you used one.
```
