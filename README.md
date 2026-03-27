# Auto-GSD: Autonomous Milestone Sprint

An autonomous coding loop that executes entire [GSD](https://github.com/gsd-build/get-shit-done) milestones end-to-end. It runs GSD's discuss/plan/execute workflow for every phase in a milestone, with parallel Opus + Codex validation at each boundary, inside a detachable tmux session.

## How it works

GSD organizes work into **milestones** containing multiple **phases**. Each phase follows a discuss &rarr; plan &rarr; execute progression. Auto-GSD wraps this into an unattended loop:

```
For each phase in the milestone:
  1. DISCUSS  — gather context, produce CONTEXT.md
  2. VALIDATE — parallel Opus (read-only) + Codex review of context
  3. PLAN     — create task plans (PLAN.md files)
  4. VALIDATE — parallel Opus + Codex review of plans
  5. EXECUTE  — run plans in wave order, atomic commits
  6. VALIDATE — parallel Opus + Codex + 3 simplify agents (reuse, quality, efficiency)
  7. VERIFY   — route on VERIFICATION.md status

After all phases:
  AUDIT → COMPLETE → CLEANUP (lifecycle)
```

### Validation gates

Each gate runs Opus and Codex concurrently, producing a 3-tier verdict:

| Verdict | Action |
|---------|--------|
| **PASS** | Continue immediately |
| **PASS_WITH_FIXES** | Consolidator fixes low-severity issues, continue |
| **FAIL** | Consolidator fixes, re-validate (max 7 rounds) |

Code validation additionally runs 3 parallel simplify agents checking **reuse** (duplicate utilities), **quality** (hacky patterns), and **efficiency** (N+1, hot-path bloat, memory leaks).

## Prerequisites

| Dependency | Purpose | Install |
|------------|---------|---------|
| [GSD](https://github.com/gsd-build/get-shit-done) | Spec engineering workflow (skills + gsd-tools) | `npx get-shit-done-cc@latest` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | CLI that the Agent SDK drives | `npm install -g @anthropic-ai/claude-code` |
| [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) | Python SDK for spawning Claude sessions | installed automatically by `uv run` |
| [uv](https://docs.astral.sh/uv/) | Python script runner (handles inline deps) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python 3.11+ | Runtime | `uv python install 3.11` |
| tmux | Detachable session | `brew install tmux` (macOS) |
| [Codex CLI](https://github.com/openai/codex) (optional) | Second-opinion validation | See repo for install |

No API keys needed — the Claude Agent SDK reuses your Claude Code auth, and Codex handles its own.

## Setup

1. **Install GSD** into your project (or globally):

   ```bash
   npx get-shit-done-cc@latest
   ```

2. **Initialize your project** with GSD:

   ```bash
   # Inside your repo
   claude
   > /gsd:new-project
   ```

   This creates `.planning/` with `PROJECT.md`, `REQUIREMENTS.md`, and `ROADMAP.md`.

3. **Clone or copy this repo** somewhere accessible:

   ```bash
   git clone <this-repo> ~/dev/auto-gsd
   ```

4. **Run the sprint:**

   ```bash
   cd ~/your-project
   bash ~/dev/auto-gsd/scripts/sprint.sh
   ```

   Or with uv directly:

   ```bash
   uv run ~/dev/auto-gsd/scripts/sprint.py
   ```

## Usage

```bash
# Run current milestone (default: yolo/AFK mode)
bash scripts/sprint.sh

# Specific milestone
bash scripts/sprint.sh v1.2

# Interactive mode — pause between phases for review
bash scripts/sprint.sh --interactive

# Skip Codex validation (faster, Opus-only)
bash scripts/sprint.sh --skip-codex

# Resume an interrupted sprint
bash scripts/sprint.sh --resume

# Auto-complete milestone when done (audit + archive + cleanup)
bash scripts/sprint.sh --complete

# Start from a specific phase
bash scripts/sprint.sh --from-phase 5

# Force restart (clears stale state)
bash scripts/sprint.sh --force
```

### Modes

| Mode | Behavior |
|------|----------|
| **YOLO** (default) | Fully autonomous. Uses defaults for uncertainties, only halts on critical errors. |
| **Interactive** | Pauses between phases for human review. |
| **No Codex** | Skips Codex validation. Opus still runs. |

### Running in tmux

When invoked through the GSD skill (`/gsd:milestone-sprint`), the sprint runs in a tmux session automatically. For standalone use:

```bash
tmux new-session -d -s gsd-milestone -c /path/to/your/project
tmux send-keys -t gsd-milestone 'bash ~/dev/auto-gsd/scripts/sprint.sh --complete' Enter

# Attach to watch progress
tmux attach -t gsd-milestone

# Detach while attached: Ctrl+b then d
```

## State tracking

The sprint creates `.planning/MILESTONE-SPRINT.md` in your project, tracking:

- Current phase and session (discuss/plan/execute)
- Per-phase status, duration, and validation results
- Checkpoints with git refs for resume
- Validation history across all rounds

## Architecture

```
sprint.sh           Thin bash launcher — tries uv, falls back to python3
sprint.py           Main async loop — phases, lifecycle, arg parsing
sprint_helpers.py   Everything else:
                      - GSD tools integration (gsd-tools.cjs subprocess calls)
                      - Claude Agent SDK session runner with retry/backoff
                      - Parallel validation orchestration (Opus + Codex + simplify)
                      - Consolidator (merges findings, drives fix sessions)
                      - State file management (MILESTONE-SPRINT.md)
                      - Prompt builders for each session type
                      - Terminal formatting
sprint_signals.py   GSD signal detection — maps output signals to exit codes
```

## License

MIT
