#!/bin/bash
# sprint.sh — Thin launcher for GSD Milestone Sprint (Python primary)
set -euo pipefail

RED="\033[31m"
YELLOW="\033[33m"
DIM="\033[2m"
BOLD="\033[1m"
RESET="\033[0m"

err()  { echo -e "  ${RED}${BOLD}ERR${RESET} $1" >&2; }
warn() { echo -e "  ${YELLOW}!!${RESET} $1" >&2; }
hint() { echo -e "  ${DIM}$1${RESET}" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Pre-flight: node (required for gsd-tools.cjs) ──
if ! command -v node &>/dev/null; then
    err "Node.js not found (required for gsd-tools.cjs)"
    hint "Install: brew install node  OR  https://nodejs.org"
    exit 1
fi

# ── Pre-flight: git ──
if ! command -v git &>/dev/null; then
    err "git not found"
    exit 1
fi

if ! git rev-parse --git-dir &>/dev/null 2>&1; then
    err "Not inside a git repository"
    hint "Run this from your project root"
    exit 1
fi

# ── Pre-flight: claude CLI ──
if ! command -v claude &>/dev/null; then
    err "Claude Code CLI not found"
    hint "Install: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

# ── Pre-flight: .planning directory ──
if [ ! -d ".planning" ]; then
    err "No .planning/ directory found in $(pwd)"
    hint "Initialize your project first: claude then /gsd:new-project"
    exit 1
fi

if [ ! -f ".planning/ROADMAP.md" ]; then
    err "No .planning/ROADMAP.md found"
    hint "Create a milestone first: /gsd:new-milestone"
    exit 1
fi

# ── Launch with uv (preferred — handles PEP 723 inline deps) ──
if command -v uv &>/dev/null; then
    exec uv run "$SCRIPT_DIR/sprint.py" "$@"
fi

# ── Fallback to python3 (requires deps pre-installed) ──
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
    if [ "$PY_VER" -lt 11 ]; then
        err "Python 3.${PY_VER} found but 3.11+ is required"
        hint "Install: uv python install 3.11"
        exit 1
    fi

    # Check that claude-agent-sdk is importable
    if ! python3 -c "import claude_agent_sdk" &>/dev/null; then
        err "claude-agent-sdk not installed (required without uv)"
        hint "Install: pip install claude-agent-sdk pyyaml"
        hint "Or install uv (recommended): curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    exec python3 "$SCRIPT_DIR/sprint.py" "$@"
fi

err "Python 3.11+ not found"
hint "Install uv (recommended): curl -LsSf https://astral.sh/uv/install.sh | sh"
hint "Then: uv python install 3.11"
exit 1
