#!/bin/bash
# install.sh — Auto-GSD installer
# Checks dependencies, installs what's missing, sets up the sprint scripts.
set -euo pipefail

BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
CYAN="\033[36m"
RESET="\033[0m"

INSTALL_DIR="${AUTO_GSD_DIR:-$HOME/.auto-gsd}"

ok()   { echo -e "  ${GREEN}OK${RESET} $1"; }
warn() { echo -e "  ${YELLOW}!!${RESET} $1"; }
fail() { echo -e "  ${RED}ERR${RESET} $1"; }
info() { echo -e "  ${DIM}$1${RESET}"; }

echo
echo -e "  ${CYAN}${BOLD}Auto-GSD Installer${RESET}"
echo -e "  ${DIM}Autonomous milestone sprint for GSD${RESET}"
echo

# ── Detect OS ──────────────────────────────────────────────

OS="$(uname -s)"
case "$OS" in
  Darwin) PM="brew";;
  Linux)
    if command -v apt-get &>/dev/null; then PM="apt"
    elif command -v dnf &>/dev/null; then PM="dnf"
    elif command -v pacman &>/dev/null; then PM="pacman"
    else PM=""
    fi;;
  *) PM="";;
esac

# ── Check dependencies ────────────────────────────────────

MISSING=()

echo -e "${BOLD}Checking dependencies...${RESET}"
echo

# Python 3.11+
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.minor}")')
  if [ "$PY_VER" -ge 11 ]; then
    ok "Python 3.${PY_VER}"
  else
    warn "Python 3.${PY_VER} found (3.11+ required)"
    MISSING+=("python")
  fi
else
  warn "Python 3 not found"
  MISSING+=("python")
fi

# uv
if command -v uv &>/dev/null; then
  ok "uv $(uv --version 2>/dev/null | head -1)"
else
  warn "uv not found"
  MISSING+=("uv")
fi

# tmux
if command -v tmux &>/dev/null; then
  ok "tmux"
else
  warn "tmux not found"
  MISSING+=("tmux")
fi

# Claude Code CLI
if command -v claude &>/dev/null; then
  ok "Claude Code CLI"
else
  warn "Claude Code CLI not found"
  MISSING+=("claude")
fi

# GSD (check common install locations)
GSD_FOUND=false
for path in \
  ".claude/get-shit-done/bin/gsd-tools.cjs" \
  "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"; do
  if [ -f "$path" ]; then
    GSD_FOUND=true
    break
  fi
done
if $GSD_FOUND; then
  ok "GSD (get-shit-done)"
else
  warn "GSD not found"
  MISSING+=("gsd")
fi

# Codex (optional)
if command -v codex &>/dev/null; then
  ok "Codex CLI (optional)"
else
  info "Codex CLI not found (optional — use --skip-codex to run without)"
fi

# API keys
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ok "ANTHROPIC_API_KEY set"
else
  warn "ANTHROPIC_API_KEY not set (required at runtime)"
fi

echo

# ── Install missing dependencies ──────────────────────────

if [ ${#MISSING[@]} -gt 0 ]; then
  echo -e "${BOLD}Installing missing dependencies...${RESET}"
  echo

  for dep in "${MISSING[@]}"; do
    case "$dep" in
      uv)
        info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        ok "uv installed"
        ;;
      python)
        if command -v uv &>/dev/null; then
          info "Installing Python 3.11 via uv..."
          uv python install 3.11
          ok "Python 3.11 installed"
        else
          fail "Install Python 3.11+ manually: https://python.org/downloads"
        fi
        ;;
      tmux)
        case "$PM" in
          brew) info "Installing tmux via brew..."; brew install tmux; ok "tmux installed";;
          apt)  info "Installing tmux via apt..."; sudo apt-get install -y tmux; ok "tmux installed";;
          dnf)  info "Installing tmux via dnf..."; sudo dnf install -y tmux; ok "tmux installed";;
          pacman) info "Installing tmux via pacman..."; sudo pacman -S --noconfirm tmux; ok "tmux installed";;
          *)    fail "Install tmux manually for your platform";;
        esac
        ;;
      claude)
        if command -v npm &>/dev/null; then
          info "Installing Claude Code CLI..."
          npm install -g @anthropic-ai/claude-code
          ok "Claude Code CLI installed"
        else
          fail "Install Claude Code CLI: npm install -g @anthropic-ai/claude-code"
        fi
        ;;
      gsd)
        if command -v npx &>/dev/null; then
          info "Installing GSD..."
          npx get-shit-done-cc@latest --claude --global
          ok "GSD installed"
        else
          fail "Install GSD: npx get-shit-done-cc@latest"
        fi
        ;;
    esac
  done

  echo
fi

# ── Install auto-gsd scripts ─────────────────────────────

echo -e "${BOLD}Installing auto-gsd to ${INSTALL_DIR}...${RESET}"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$INSTALL_DIR/scripts"

cp "$SCRIPT_DIR/scripts/sprint.py"         "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/sprint_helpers.py" "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/sprint_signals.py" "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/sprint.sh"         "$INSTALL_DIR/scripts/"
chmod +x "$INSTALL_DIR/scripts/sprint.sh" "$INSTALL_DIR/scripts/sprint.py"

cp "$SCRIPT_DIR/SKILL.md"                 "$INSTALL_DIR/"
cp "$SCRIPT_DIR/milestone-completion.md"  "$INSTALL_DIR/"
cp "$SCRIPT_DIR/README.md"               "$INSTALL_DIR/" 2>/dev/null || true

ok "Scripts installed"

# ── Create convenience alias ──────────────────────────────

SHELL_RC=""
case "${SHELL:-}" in
  */zsh)  SHELL_RC="$HOME/.zshrc";;
  */bash) SHELL_RC="$HOME/.bashrc";;
esac

ALIAS_LINE="alias auto-gsd='bash $INSTALL_DIR/scripts/sprint.sh'"

if [ -n "$SHELL_RC" ]; then
  if ! grep -qF "auto-gsd" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# Auto-GSD: autonomous milestone sprint" >> "$SHELL_RC"
    echo "$ALIAS_LINE" >> "$SHELL_RC"
    ok "Alias added to $SHELL_RC"
    info "Run: source $SHELL_RC"
  else
    info "Alias already exists in $SHELL_RC"
  fi
fi

# ── Done ──────────────────────────────────────────────────

echo
echo -e "  ${GREEN}${BOLD}+----------------------------------+${RESET}"
echo -e "  ${GREEN}${BOLD}|      Auto-GSD installed          |${RESET}"
echo -e "  ${GREEN}${BOLD}+----------------------------------+${RESET}"
echo
echo -e "  ${BOLD}Usage:${RESET}"
echo -e "    cd /path/to/your/project"
echo -e "    auto-gsd                    ${DIM}# run current milestone${RESET}"
echo -e "    auto-gsd --interactive      ${DIM}# pause between phases${RESET}"
echo -e "    auto-gsd --skip-codex       ${DIM}# opus-only validation${RESET}"
echo -e "    auto-gsd --complete         ${DIM}# auto-finalize milestone${RESET}"
echo
echo -e "  ${DIM}Installed to: $INSTALL_DIR${RESET}"
echo -e "  ${DIM}Set AUTO_GSD_DIR to customize install location${RESET}"
echo
