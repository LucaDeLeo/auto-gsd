---
name: gsd:milestone-sprint
description: Run entire milestone autonomously with Codex validation. Auto-detects current milestone, executes all phases, and runs audit.
argument-hint: '[milestone] [--interactive] [--skip-codex] [--resume] [--complete] [--from-phase N] [--force]'
---

# Milestone Sprint

Run an entire milestone autonomously, from current position to milestone completion. Uses Python + Claude Agent SDK with parallel Opus+Codex validation at discuss/plan/execute boundaries.

## How It Differs from `/gsd:autonomous`

| Aspect        | `/gsd:autonomous`              | `/gsd:milestone-sprint`                        |
| ------------- | ------------------------------ | ---------------------------------------------- |
| Runtime       | Inline (Claude session)        | tmux (detachable Python process)               |
| Validation    | None                           | Parallel Opus + Codex at 3 boundaries          |
| Verdicts      | Binary (pass/fail)             | 3-tier: PASS / PASS_WITH_FIXES / FAIL          |
| Cost tracking | None                           | Per-session and sprint-level usage + burn rate  |
| Fix loops     | Manual                         | Automatic consolidator (max 7 rounds)          |
| Completion    | Suggests next steps            | Optionally runs audit → complete → cleanup     |
| State         | STATE.md (GSD-managed)         | MILESTONE-SPRINT.md (sprint-level tracking)    |
| Resume        | `--from N`                     | `--resume` from checkpoint                     |

## Usage

```bash
# Run current milestone in YOLO mode (default)
/gsd:milestone-sprint

# Run specific milestone
/gsd:milestone-sprint v1.2

# Interactive mode (pause between phases)
/gsd:milestone-sprint --interactive

# Skip Codex validation (faster, less safe)
/gsd:milestone-sprint --skip-codex

# Resume interrupted sprint
/gsd:milestone-sprint --resume

# Auto-complete milestone when done (audit → complete → cleanup)
/gsd:milestone-sprint --complete

# Start from a specific phase
/gsd:milestone-sprint --from-phase 5

# Force restart (remove stale state)
/gsd:milestone-sprint --force

# Set weekly budget cap
/gsd:milestone-sprint --budget 100
```

## Flow

1. **Detect milestone** via `gsd-tools.cjs init milestone-op`
2. **Discover phases** via `gsd-tools.cjs roadmap analyze` (structured JSON, handles decimals)
3. **For each phase:**
   - **Discuss** — Claude SDK session invoking `/gsd:discuss-phase --auto`
   - **Validation 1** — Opus (read-only) + Codex in parallel → CONTEXT quality
   - **Plan** — Claude SDK session invoking `/gsd:plan-phase`
   - **Validation 2** — Opus + Codex in parallel → PLAN quality
   - **Execute** — Claude SDK session invoking `/gsd:execute-phase --no-transition`
   - **Validation 3** — Opus + Codex in parallel → code quality
   - **Verify** — Route on VERIFICATION.md status (passed/human_needed/gaps_found)
4. **Re-read ROADMAP.md** after each phase (catches inserted decimal phases)
5. **Lifecycle** — audit → complete → cleanup (if `--complete`)

## Validation (Parallel Opus + Codex)

Each validation gate runs Opus and Codex concurrently:

| Verdict          | Action                                    |
| ---------------- | ----------------------------------------- |
| PASS             | Continue immediately                      |
| PASS_WITH_FIXES  | Consolidator fixes low-severity, continue |
| FAIL             | Consolidator fixes, re-validate (max 7x)  |

## Modes

| Mode                              | Behavior                                                                    |
| --------------------------------- | --------------------------------------------------------------------------- |
| **YOLO** (default)                | Auto-continue, use defaults for uncertainties, only halt on critical issues |
| **Interactive** (`--interactive`) | Pause between phases, prompt on warnings                                    |
| **No Codex** (`--skip-codex`)     | Skip Codex validation (Opus still runs)                                     |

## State File

Creates `.planning/MILESTONE-SPRINT.md` with progress tracking:

```yaml
---
started: '2024-01-25T...'
milestone: 'v1.2'
milestone_name: 'Org CRM'
mode: yolo
phase_count: 6
current_phase: '13'
status: running
auto_complete: false
phases_completed: 2
---
```

## Execution

This runs in a tmux session so you can detach and reattach.

```bash
ARGS="$ARGUMENTS"
SESSION_NAME="gsd-milestone"

# Find the script (project-local or global)
if [[ -f ".claude/skills/gsd-milestone-sprint/scripts/sprint.sh" ]]; then
  SCRIPT=".claude/skills/gsd-milestone-sprint/scripts/sprint.sh"
else
  SCRIPT="$HOME/.claude/skills/gsd-milestone-sprint/scripts/sprint.sh"
fi

# Kill existing session if running, create new one
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
tmux new-session -d -s "$SESSION_NAME" -c "$(pwd)"

# Run the milestone sprint script in the tmux session
tmux send-keys -t "$SESSION_NAME" "bash $SCRIPT $ARGS" Enter

# Tell user how to attach
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Milestone Sprint started in tmux session: $SESSION_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "To attach and watch progress:"
echo "  tmux attach -t $SESSION_NAME"
echo ""
echo "To detach (while attached):"
echo "  Ctrl+b then d"
echo ""
echo "To check if still running:"
echo "  tmux has-session -t $SESSION_NAME 2>/dev/null && echo 'Running' || echo 'Finished'"
echo ""
```
