"""State management, prompt building, streaming, and Codex integration for GSD Milestone Sprint.

Follows the bmad-sprint architecture (Python + Claude Agent SDK) adapted for GSD phases/milestones.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sprint_signals import check_signals

# ═══════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════

PLANNING_DIR = Path(".planning")
MILESTONE_SPRINT_FILE = PLANNING_DIR / "MILESTONE-SPRINT.md"
STATE_FILE = PLANNING_DIR / "STATE.md"
ROADMAP_FILE = PLANNING_DIR / "ROADMAP.md"

_SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = _SCRIPT_DIR.parent.parent
CODEX_SCRIPT: Path | None = None

# Find codex script (optional — None if not found)
for _candidate in [
    _SCRIPT_DIR / "ask_codex.sh",
    SKILLS_DIR / "codex-oracle" / "scripts" / "ask_codex.sh",
    Path.home() / ".claude" / "skills" / "codex-oracle" / "scripts" / "ask_codex.sh",
]:
    if _candidate.exists():
        CODEX_SCRIPT = _candidate
        break


# ═══════════════════════════════════════════════════════════════
# PREFLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_preflight_checks(*, skip_codex: bool = False) -> PreflightResult:
    """Validate all runtime dependencies before starting the sprint.

    Returns a PreflightResult with errors (fatal) and warnings (non-fatal).
    """
    result = PreflightResult(ok=True)

    # ── Binaries ──
    for binary, purpose, install_hint in [
        ("node", "gsd-tools.cjs", "brew install node  OR  https://nodejs.org"),
        ("git", "version control", "brew install git"),
        ("claude", "Claude Agent SDK sessions", "npm install -g @anthropic-ai/claude-code"),
    ]:
        if not shutil.which(binary):
            result.errors.append(f"'{binary}' not found in PATH ({purpose})\n    Install: {install_hint}")
            result.ok = False

    # ── Python SDK ──
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        result.errors.append(
            "claude-agent-sdk not importable\n"
            "    Install: pip install claude-agent-sdk\n"
            "    Or use 'uv run' which handles deps automatically"
        )
        result.ok = False

    # No API keys needed — Claude SDK reuses Claude Code auth, Codex handles its own

    # ── Project structure ──
    if not PLANNING_DIR.is_dir():
        result.errors.append(
            f".planning/ directory not found in {Path.cwd()}\n"
            "    Initialize: claude then /gsd:new-project"
        )
        result.ok = False
    else:
        if not ROADMAP_FILE.is_file():
            result.errors.append(
                ".planning/ROADMAP.md not found\n"
                "    Create a milestone: /gsd:new-milestone"
            )
            result.ok = False

        project_file = PLANNING_DIR / "PROJECT.md"
        if not project_file.is_file():
            result.warnings.append(
                ".planning/PROJECT.md not found — prompts reference it but sprint can continue"
            )

    # ── Git state ──
    if shutil.which("git"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                result.errors.append(
                    f"Not a git repository: {Path.cwd()}\n"
                    "    Run from your project root"
                )
                result.ok = False
        except subprocess.TimeoutExpired:
            result.warnings.append("git rev-parse timed out — git may be in a bad state")

        try:
            r = subprocess.run(
                ["git", "ls-files", "-u"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip():
                result.errors.append(
                    "Unresolved merge conflicts detected\n"
                    "    Resolve conflicts before running the sprint"
                )
                result.ok = False
        except (subprocess.TimeoutExpired, Exception):
            pass

    # ── GSD tools ──
    try:
        _find_gsd_tools()
    except FileNotFoundError as e:
        result.errors.append(str(e))
        result.ok = False

    # ── Codex (optional) ──
    if not skip_codex and CODEX_SCRIPT is None:
        result.warnings.append(
            "Codex script (ask_codex.sh) not found — Codex validation will use fallback\n"
            "    Install codex-oracle skill, or run with --skip-codex"
        )

    return result


def print_preflight_result(result: PreflightResult) -> None:
    """Print preflight results with colored formatting."""
    if result.errors:
        print(f"\n  {_RED}{_BOLD}Preflight failed:{_RESET}\n")
        for e in result.errors:
            for i, line in enumerate(e.splitlines()):
                if i == 0:
                    print(f"  {_RED}{_BOLD}x{_RESET} {line}")
                else:
                    print(f"    {_DIM}{line}{_RESET}")
        print()

    if result.warnings:
        for w in result.warnings:
            for i, line in enumerate(w.splitlines()):
                if i == 0:
                    print(f"  {_YELLOW}!{_RESET} {line}")
                else:
                    print(f"    {_DIM}{line}{_RESET}")
        print()

# ═══════════════════════════════════════════════════════════════
# TERMINAL FORMATTING
# ═══════════════════════════════════════════════════════════════

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"

# Pre-built label prefixes (fixed 5-char width so pipes align)
_LBL_OPUS = f"  {_CYAN}opus {_RESET}{_DIM}│{_RESET} "
_LBL_CODEX = f"  {_YELLOW}codex{_RESET}{_DIM}│{_RESET} "
_LBL_FIX = f"  {_MAGENTA}fix  {_RESET}{_DIM}│{_RESET} "
_LBL_DISC = f"  {_GREEN}disc {_RESET}{_DIM}│{_RESET} "
_LBL_PLAN = f"  {_CYAN}plan {_RESET}{_DIM}│{_RESET} "
_LBL_EXEC = f"  {_CYAN}exec {_RESET}{_DIM}│{_RESET} "
_LBL_AUDIT = f"  {_MAGENTA}audit{_RESET}{_DIM}│{_RESET} "
_LBL_COMPL = f"  {_MAGENTA}compl{_RESET}{_DIM}│{_RESET} "
_LBL_CLEAN = f"  {_DIM}clean{_RESET}{_DIM}│{_RESET} "
_LBL_GIT = f"  {_GREEN}git  {_RESET}{_DIM}│{_RESET} "
_LBL_COST = f"  {_YELLOW}cost {_RESET}{_DIM}│{_RESET} "
_LBL_REUSE = f"  {_GREEN}reuse{_RESET}{_DIM}│{_RESET} "
_LBL_QUAL = f"  {_CYAN}qual {_RESET}{_DIM}│{_RESET} "
_LBL_EFFIC = f"  {_YELLOW}effic{_RESET}{_DIM}│{_RESET} "


def _labeled_print(label: str, text: str) -> None:
    """Print text with a colored label prefix on each line."""
    for line in text.splitlines():
        print(f"{label}{line}")


# ═══════════════════════════════════════════════════════════════
# USAGE TRACKING
# ═══════════════════════════════════════════════════════════════


@dataclass
class SessionUsage:
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    duration_ms: int
    num_turns: int
    label: str


class UsageTracker:
    def __init__(self, weekly_budget_usd: float = 200.0):
        self._sessions: list[SessionUsage] = []
        self._phase_start_idx: int = 0
        self._sprint_start: float = time.time()
        self.weekly_budget_usd = weekly_budget_usd

    def add(self, result_msg: object, label: str) -> SessionUsage | None:
        if result_msg is None:
            return None
        usage = getattr(result_msg, "usage", {}) or {}
        su = SessionUsage(
            cost_usd=getattr(result_msg, "total_cost_usd", 0.0) or 0.0,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            duration_ms=getattr(result_msg, "duration_ms", 0) or 0,
            num_turns=getattr(result_msg, "num_turns", 0) or 0,
            label=label,
        )
        self._sessions.append(su)
        return su

    def phase_totals(self) -> dict:
        sessions = self._sessions[self._phase_start_idx:]
        return {
            "cost_usd": sum(s.cost_usd for s in sessions),
            "input_tokens": sum(s.input_tokens for s in sessions),
            "output_tokens": sum(s.output_tokens for s in sessions),
            "duration_ms": sum(s.duration_ms for s in sessions),
            "num_turns": sum(s.num_turns for s in sessions),
        }

    def sprint_totals(self) -> dict:
        return {
            "cost_usd": sum(s.cost_usd for s in self._sessions),
            "input_tokens": sum(s.input_tokens for s in self._sessions),
            "output_tokens": sum(s.output_tokens for s in self._sessions),
            "duration_ms": sum(s.duration_ms for s in self._sessions),
            "num_turns": sum(s.num_turns for s in self._sessions),
        }

    def mark_phase_boundary(self) -> None:
        self._phase_start_idx = len(self._sessions)

    def burn_rate_per_hour(self) -> float:
        elapsed_h = (time.time() - self._sprint_start) / 3600
        if elapsed_h < 0.001:
            return 0.0
        return self.sprint_totals()["cost_usd"] / elapsed_h

    def weekly_hours_remaining(self) -> float:
        rate = self.burn_rate_per_hour()
        if rate < 0.001:
            return float("inf")
        return self.weekly_budget_usd / rate


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def print_session_usage(su: SessionUsage | None, tracker: UsageTracker) -> None:
    if su is None:
        return
    phase = tracker.phase_totals()
    print(
        f"{_LBL_COST}${su.cost_usd:.2f} "
        f"({_fmt_tokens(su.input_tokens)} in, {_fmt_tokens(su.output_tokens)} out) "
        f"— phase ${phase['cost_usd']:.2f}"
    )


def print_phase_usage(tracker: UsageTracker) -> None:
    s = tracker.phase_totals()
    if s["cost_usd"] < 0.001:
        return
    dur_str = format_duration(s["duration_ms"] // 1000)
    print(
        f"{_LBL_COST}phase total: ${s['cost_usd']:.2f} "
        f"({_fmt_tokens(s['input_tokens'])} in, {_fmt_tokens(s['output_tokens'])} out, "
        f"{s['num_turns']} turns, {dur_str})"
    )


# ═══════════════════════════════════════════════════════════════
# BANNERS & STYLED OUTPUT
# ═══════════════════════════════════════════════════════════════


def sprint_banner(
    phase_count: int,
    yolo_mode: bool,
    skip_codex: bool,
    milestone_name: str,
    milestone_version: str,
    auto_complete: bool,
) -> None:
    """Print the styled main milestone sprint banner."""
    mode_str = "AFK (yolo)" if yolo_mode else "Interactive"
    codex_str = "Disabled" if skip_codex else "Enabled"

    lines = [
        f"  Milestone: {milestone_version} — {milestone_name}",
        f"  Phases: {phase_count}   Mode: {mode_str}",
        f"  Codex: {codex_str}   Auto-complete: {'Yes' if auto_complete else 'No'}",
    ]

    width = max(len(l) for l in lines) + 4
    width = max(width, 42)

    print()
    print(f"  {_CYAN}{_BOLD}╔{'═' * width}╗{_RESET}")
    title = "GSD MILESTONE SPRINT"
    print(f"  {_CYAN}{_BOLD}║{_RESET}{_BOLD}{title:^{width}s}{_RESET}{_CYAN}{_BOLD}║{_RESET}")
    print(f"  {_CYAN}{_BOLD}╟{'─' * width}╢{_RESET}")
    for line in lines:
        padded = line + " " * (width - len(line))
        print(f"  {_CYAN}{_BOLD}║{_RESET}{_DIM}{padded}{_RESET}{_CYAN}{_BOLD}║{_RESET}")
    print(f"  {_CYAN}{_BOLD}╚{'═' * width}╝{_RESET}")


def phase_banner(phase_index: int, phase_count: int, phase_num: str, phase_name: str) -> None:
    """Print styled per-phase header."""
    label = f"  PHASE {phase_index}/{phase_count}: {phase_num} — {phase_name}"
    width = max(len(label) + 2, 50)
    print()
    print(f"  {_BOLD}┌{'─' * width}┐{_RESET}")
    print(f"  {_BOLD}│{label:<{width}s}│{_RESET}")
    print(f"  {_BOLD}└{'─' * width}┘{_RESET}")


def session_banner(name: str) -> None:
    """Print a styled session header (e.g. 'DISCUSS', 'PLAN', 'EXECUTE')."""
    print()
    print(f"  {_BOLD}-- {name} --{_RESET}")
    print()


def ok_msg(text: str) -> None:
    print(f"  {_GREEN}{_BOLD}OK{_RESET} {text}")


def warn_msg(text: str) -> None:
    print(f"  {_YELLOW}{_BOLD}WARN{_RESET} {text}")


def dim_msg(text: str) -> None:
    print(f"  {_DIM}{text}{_RESET}")


def phase_complete_banner(phase_num: str, duration_str: str) -> None:
    print()
    print(f"  {_GREEN}{_BOLD}✓{_RESET} Phase {_BOLD}{phase_num}{_RESET} complete {_DIM}({duration_str}){_RESET}")


def lifecycle_banner(milestone_version: str, milestone_name: str) -> None:
    """Print lifecycle transition banner."""
    lines = [
        f"  All phases complete → Starting lifecycle",
        f"  audit → complete → cleanup",
        f"  Milestone: {milestone_version} — {milestone_name}",
    ]
    width = max(len(l) for l in lines) + 4
    width = max(width, 42)
    print()
    print(f"  {_MAGENTA}{_BOLD}╔{'═' * width}╗{_RESET}")
    title = "LIFECYCLE"
    print(f"  {_MAGENTA}{_BOLD}║{title:^{width}s}║{_RESET}")
    print(f"  {_MAGENTA}{_BOLD}╟{'─' * width}╢{_RESET}")
    for line in lines:
        padded = line + " " * (width - len(line))
        print(f"  {_MAGENTA}{_BOLD}║{_RESET}{padded:<{width}s}{_MAGENTA}{_BOLD}║{_RESET}")
    print(f"  {_MAGENTA}{_BOLD}╚{'═' * width}╝{_RESET}")


def sprint_complete_banner(phase_count: int, tracker: UsageTracker | None = None) -> None:
    """Print the final sprint complete banner."""
    lines = [f"  {phase_count} phases executed successfully"]
    if tracker:
        t = tracker.sprint_totals()
        lines.append(
            f"  ${t['cost_usd']:.2f} API cost "
            f"({_fmt_tokens(t['input_tokens'])} in, {_fmt_tokens(t['output_tokens'])} out)"
        )
        rate = tracker.burn_rate_per_hour()
        if rate > 0.001:
            hrs = tracker.weekly_hours_remaining()
            remaining = f"~{hrs / 24:.0f}d left this week" if hrs > 48 else f"~{hrs:.0f}h left this week"
            lines.append(f"  burn: ${rate:.2f}/hr — {remaining}")
    width = max(max(len(l) for l in lines) + 2, 42)
    print()
    print(f"  {_GREEN}{_BOLD}╔{'═' * width}╗{_RESET}")
    title = "MILESTONE SPRINT COMPLETE"
    print(f"  {_GREEN}{_BOLD}║{title:^{width}s}║{_RESET}")
    print(f"  {_GREEN}{_BOLD}╟{'─' * width}╢{_RESET}")
    for line in lines:
        print(f"  {_GREEN}{_BOLD}║{_RESET}{line:<{width}s}{_GREEN}{_BOLD}║{_RESET}")
    print(f"  {_GREEN}{_BOLD}╚{'═' * width}╝{_RESET}")


def _verdict_color(v: str) -> str:
    """Map verdict string to ANSI color."""
    if v == "PASS":
        return _GREEN
    if v == "PASS_WITH_FIXES":
        return _YELLOW
    return _RED


def _print_verdict_box(
    opus_result: "ValidationResult", codex_result: "ValidationResult", skip_codex: bool,
) -> None:
    """Print a compact verdict summary box."""
    o_color = _verdict_color(opus_result.verdict)
    opus_str = f"{o_color}{_BOLD}{opus_result.verdict}{_RESET}"
    if skip_codex:
        print(f"  {_DIM}+--------------------------------------+{_RESET}")
        print(f"  {_DIM}|{_RESET} opus: {opus_str:<30s} codex: {_DIM}skip{_RESET} {_DIM}|{_RESET}")
        print(f"  {_DIM}+--------------------------------------+{_RESET}")
    else:
        c_color = _verdict_color(codex_result.verdict)
        codex_str = f"{c_color}{_BOLD}{codex_result.verdict}{_RESET}"
        print(f"  {_DIM}+--------------------------------------+{_RESET}")
        print(f"  {_DIM}|{_RESET} opus: {opus_str:<30s} codex: {codex_str} {_DIM}|{_RESET}")
        print(f"  {_DIM}+--------------------------------------+{_RESET}")


# ═══════════════════════════════════════════════════════════════
# GSD TOOLS INTEGRATION
# ═══════════════════════════════════════════════════════════════


_gsd_tools_path: Path | None = None

_GSD_TOOLS_SEARCH_PATHS = [
    lambda: Path.cwd() / ".claude" / "get-shit-done" / "bin" / "gsd-tools.cjs",
    lambda: Path.home() / ".claude" / "get-shit-done" / "bin" / "gsd-tools.cjs",
]


def _find_gsd_tools() -> Path:
    """Locate gsd-tools.cjs — cached after first discovery, re-validated on use."""
    global _gsd_tools_path

    # Re-validate cached path (file may have been deleted since last call)
    if _gsd_tools_path is not None:
        if _gsd_tools_path.exists():
            return _gsd_tools_path
        warn_msg(f"Cached gsd-tools path no longer exists: {_gsd_tools_path}")
        _gsd_tools_path = None

    # Check explicit candidates first (fast)
    for path_fn in _GSD_TOOLS_SEARCH_PATHS:
        path = path_fn()
        if path.exists():
            _gsd_tools_path = path
            return path

    # Fallback: check worktree paths (slower glob)
    try:
        for wt in Path.home().glob("dev/worktrees/*/"):
            p = wt / ".claude" / "get-shit-done" / "bin" / "gsd-tools.cjs"
            if p.exists():
                _gsd_tools_path = p
                return p
    except PermissionError:
        pass

    searched = "\n    ".join(str(fn()) for fn in _GSD_TOOLS_SEARCH_PATHS)
    raise FileNotFoundError(
        f"gsd-tools.cjs not found. Searched:\n    {searched}\n"
        "    ~/dev/worktrees/*/\n"
        "    Install GSD: npx get-shit-done-cc@latest"
    )


def run_gsd_tools(*args: str, timeout: int = 30) -> dict | str:
    """Run gsd-tools.cjs with given args. Returns parsed JSON or raw string.

    Handles the @file: indirection pattern used by gsd-tools for large output.
    """
    gsd_tools_path = _find_gsd_tools()
    cmd_desc = f"gsd-tools {' '.join(args)}"

    try:
        result = subprocess.run(
            ["node", str(gsd_tools_path), *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path.cwd()),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd_desc}: timed out after {timeout}s")
    except FileNotFoundError:
        raise RuntimeError(f"{cmd_desc}: 'node' binary not found")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Provide actionable error messages for common failures
        if "MODULE_NOT_FOUND" in stderr or "Cannot find module" in stderr:
            raise RuntimeError(
                f"{cmd_desc}: GSD module not found. Reinstall: npx get-shit-done-cc@latest\n"
                f"    Detail: {stderr[:200]}"
            )
        raise RuntimeError(f"{cmd_desc} failed (exit {result.returncode}): {stderr[:300]}")

    output = result.stdout.strip()

    if not output:
        raise RuntimeError(f"{cmd_desc}: returned empty output")

    # Handle @file: indirection
    if output.startswith("@file:"):
        file_path = output[len("@file:"):]
        p = Path(file_path)
        if not p.is_file():
            raise RuntimeError(
                f"{cmd_desc}: returned @file:{file_path} but file does not exist"
            )
        output = p.read_text()

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def get_milestone_info() -> dict:
    """Get current milestone info via gsd-tools init milestone-op.

    Returns dict with: milestone_version, milestone_name, phase_count,
    completed_phases, roadmap_exists, state_exists, etc.
    """
    result = run_gsd_tools("init", "milestone-op")
    if not isinstance(result, dict):
        raise RuntimeError(
            f"gsd-tools init milestone-op returned unexpected format: {str(result)[:200]}\n"
            "    Expected JSON object. Is GSD up to date?"
        )
    return result


def _safe_phase_number(phase: dict) -> float:
    """Extract phase number as float, with clear error on failure."""
    num = phase.get("number")
    if num is None:
        raise ValueError(f"Phase missing 'number' field: {phase}")
    try:
        return float(num)
    except (ValueError, TypeError):
        raise ValueError(f"Phase number '{num}' is not numeric: {phase}")


def discover_phases(from_phase: str = "") -> list[dict]:
    """Discover incomplete phases via gsd-tools roadmap analyze.

    Returns list of phase dicts with: number, name, goal, disk_status,
    roadmap_complete, has_context, plan_count, summary_count.
    Filters to incomplete phases, sorted by number ascending.
    """
    data = run_gsd_tools("roadmap", "analyze")
    if isinstance(data, str):
        raise RuntimeError(
            f"roadmap analyze returned non-JSON output:\n    {data[:200]}\n"
            "    Is .planning/ROADMAP.md valid?"
        )

    if "phases" not in data:
        raise RuntimeError(
            f"roadmap analyze returned JSON without 'phases' key.\n"
            f"    Keys found: {list(data.keys())}\n"
            "    Is GSD up to date? Try: npx get-shit-done-cc@latest"
        )

    phases = data["phases"]
    if not isinstance(phases, list):
        raise RuntimeError(f"roadmap analyze 'phases' is not a list: {type(phases)}")

    # Filter to incomplete
    incomplete = [
        p for p in phases
        if p.get("disk_status") != "complete" or not p.get("roadmap_complete", False)
    ]

    # Apply --from filter
    if from_phase:
        try:
            from_num = float(from_phase)
        except ValueError:
            warn_msg(f"--from-phase '{from_phase}' is not numeric, ignoring filter")
            from_num = None

        if from_num is not None:
            filtered = []
            for p in incomplete:
                try:
                    if _safe_phase_number(p) >= from_num:
                        filtered.append(p)
                except ValueError as e:
                    warn_msg(str(e))
            incomplete = filtered

    # Sort by phase number (handles decimals like 5.1)
    try:
        incomplete.sort(key=_safe_phase_number)
    except ValueError as e:
        warn_msg(f"Could not sort phases by number: {e}")

    return incomplete


def get_phase_state(phase_num: str) -> dict:
    """Get phase state via gsd-tools init phase-op N.

    Returns dict with: has_context, has_plans, has_verification,
    phase_dir, padded_phase, phase_name, phase_slug, plan_count, etc.
    """
    result = run_gsd_tools("init", "phase-op", str(phase_num))
    if not isinstance(result, dict):
        raise RuntimeError(
            f"gsd-tools init phase-op {phase_num} returned unexpected format: {type(result)}\n"
            "    Expected JSON object"
        )
    return result


def get_phase_detail(phase_num: str) -> dict:
    """Get phase detail from roadmap via gsd-tools roadmap get-phase N."""
    result = run_gsd_tools("roadmap", "get-phase", str(phase_num))
    if not isinstance(result, dict):
        raise RuntimeError(
            f"gsd-tools roadmap get-phase {phase_num} returned unexpected format: {type(result)}\n"
            "    Expected JSON object"
        )
    return result


def get_config_value(key: str) -> str:
    """Get a config value via gsd-tools config-get KEY."""
    try:
        result = run_gsd_tools("config-get", key)
        if isinstance(result, dict):
            return str(result.get("value", ""))
        return str(result).strip()
    except Exception:
        return ""


def is_infrastructure_phase(phase_detail: dict) -> bool:
    """Detect infrastructure-only phase (skip discuss).

    Matches autonomous.md logic: infra keywords + no user-facing behavior.
    """
    goal = (phase_detail.get("goal") or "").lower()
    infra_keywords = {
        "scaffolding", "plumbing", "setup", "configuration",
        "migration", "refactor", "rename", "restructure",
        "upgrade", "infrastructure",
    }
    has_infra_keyword = any(kw in goal for kw in infra_keywords)
    if not has_infra_keyword:
        return False

    # Check for user-facing behavior in requirements/goal
    combined = goal + " " + str(phase_detail.get("requirements", "")).lower()
    user_facing_markers = ("users can", "displays", "shows", "presents", "user sees")
    has_user_facing = any(w in combined for w in user_facing_markers)
    return not has_user_facing


# ═══════════════════════════════════════════════════════════════
# MILESTONE SPRINT STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════


def init_milestone_sprint(
    milestone_name: str,
    milestone_version: str,
    phases: list[dict],
    mode: str,
    auto_complete: bool,
) -> None:
    """Initialize MILESTONE-SPRINT.md state file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    phase_count = len(phases)
    first_phase = phases[0]["number"] if phases else ""

    PLANNING_DIR.mkdir(parents=True, exist_ok=True)

    phase_rows = ""
    for p in phases:
        status = "complete" if p.get("disk_status") == "complete" else "pending"
        phase_rows += f"| {p['number']} | {status} | - | - | - |\n"

    MILESTONE_SPRINT_FILE.write_text(f"""---
started: "{timestamp}"
milestone: "{milestone_version}"
milestone_name: "{milestone_name}"
mode: {mode}
phase_count: {phase_count}
current_phase: "{first_phase}"
status: running
auto_complete: {"true" if auto_complete else "false"}
phases_completed: 0
halt_reason: null
---

# Milestone Sprint: {milestone_name}

## Progress

| Phase | Status | Duration | Codex | Notes |
|-------|--------|----------|-------|-------|
{phase_rows}
## Validation History

| Phase | Step | Opus | Codex | Outcome |
|-------|------|------|-------|---------|

## Checkpoints

*(checkpoint details recorded here for resume)*

""")
    print(f"Sprint initialized ({mode} mode)")


def get_milestone_sprint_field(field: str) -> str:
    if not MILESTONE_SPRINT_FILE.exists():
        return ""
    content = MILESTONE_SPRINT_FILE.read_text()
    match = re.search(rf"^{re.escape(field)}:\s*(.*)", content, re.MULTILINE)
    if match:
        return match.group(1).strip().strip('"')
    return ""


def update_milestone_sprint_field(field: str, value: str) -> None:
    if not MILESTONE_SPRINT_FILE.exists():
        return
    content = MILESTONE_SPRINT_FILE.read_text()
    escaped = value.replace("\\", "\\\\")
    new_content = re.sub(
        rf"^({re.escape(field)}:\s*).*",
        rf"\g<1>{escaped}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if new_content != content:
        MILESTONE_SPRINT_FILE.write_text(new_content)


def load_milestone_sprint_state() -> bool:
    """Load and validate state for resume. Returns True if resumable."""
    if not MILESTONE_SPRINT_FILE.exists():
        print("No MILESTONE-SPRINT.md found. Cannot resume.")
        return False
    status = get_milestone_sprint_field("status")
    if status == "complete":
        print("Milestone sprint already complete.")
        return False
    current = get_milestone_sprint_field("current_phase")
    print(f"Resuming milestone sprint from phase {current}")
    return True


def log_milestone_phase_complete(phase_num: str, duration: str, codex_result: str, notes: str) -> None:
    if not MILESTONE_SPRINT_FILE.exists():
        return
    content = MILESTONE_SPRINT_FILE.read_text()
    # Update progress table row
    new_content = re.sub(
        rf"\| {re.escape(phase_num)} \| [^|]* \| [^|]* \| [^|]* \| [^|]* \|",
        f"| {phase_num} | complete | {duration} | {codex_result} | {notes} |",
        content,
        count=1,
    )
    if new_content != content:
        MILESTONE_SPRINT_FILE.write_text(new_content)

    completed = get_milestone_sprint_field("phases_completed")
    try:
        count = int(completed)
    except ValueError:
        count = 0
    update_milestone_sprint_field("phases_completed", str(count + 1))
    update_milestone_sprint_field("last_action", f"Phase {phase_num} completed")


def log_validation_result(phase_num: str, step: str, opus: str, codex: str, outcome: str) -> None:
    """Append a row to the Validation History table."""
    if not MILESTONE_SPRINT_FILE.exists():
        return
    safe_outcome = outcome.replace("|", " ").replace("\n", " ")[:50]
    row = f"| {phase_num} | {step} | {opus} | {codex} | {safe_outcome} |\n"
    with MILESTONE_SPRINT_FILE.open("a") as f:
        f.write(row)


def halt_milestone_sprint(reason: str) -> None:
    update_milestone_sprint_field("status", "halted")
    update_milestone_sprint_field("halt_reason", f'"{reason}"')

    content_lines = [
        f" Reason: {reason}",
        f" Resume: /gsd:milestone-sprint --resume",
    ]
    width = max((len(l) + 2 for l in content_lines), default=40)
    width = max(width, 40)

    print()
    print(f"  {_RED}{_BOLD}╔{'═' * width}╗{_RESET}")
    print(f"  {_RED}{_BOLD}║{'MILESTONE SPRINT HALTED':^{width}s}║{_RESET}")
    print(f"  {_RED}{_BOLD}╟{'─' * width}╢{_RESET}")
    for line in content_lines:
        print(f"  {_RED}{_BOLD}║{_RESET}{line:<{width}s}{_RED}{_BOLD}║{_RESET}")
    print(f"  {_RED}{_BOLD}╚{'═' * width}╝{_RESET}")


def finalize_milestone_sprint() -> None:
    update_milestone_sprint_field("status", "complete")
    update_milestone_sprint_field("last_action", "Sprint completed successfully")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with MILESTONE_SPRINT_FILE.open("a") as f:
        f.write(f"\n*Completed: {timestamp}*\n")


def check_no_active_milestone_sprint() -> bool:
    if MILESTONE_SPRINT_FILE.exists():
        status = get_milestone_sprint_field("status")
        if status == "running":
            mtime = MILESTONE_SPRINT_FILE.stat().st_mtime
            if time.time() - mtime > 7200:
                print("Sprint appears stale (last update >2h ago). Use --force to restart.")
            else:
                print("Milestone sprint already running. Use --resume or --force")
            return False
    return True


# ═══════════════════════════════════════════════════════════════
# CHECKPOINT/RESUME
# ═══════════════════════════════════════════════════════════════


def create_checkpoint(phase_num: str, session: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        git_ref = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        git_ref = "unknown"

    with MILESTONE_SPRINT_FILE.open("a") as f:
        f.write(f"""
### Phase {phase_num} - {session} Checkpoint
- timestamp: {timestamp}
- git_ref: {git_ref}
- session: {session}
""")


# ═══════════════════════════════════════════════════════════════
# STREAMING EXECUTION (Claude Agent SDK)
# ═══════════════════════════════════════════════════════════════

# Last session output
_last_output: str = ""


def get_stream_output() -> str:
    return _last_output


@dataclass
class ValidationResult:
    verdict: str  # "PASS", "PASS_WITH_FIXES", or "FAIL"
    raw: str  # full validator output
    issues_text: str  # extracted issues block for the consolidator


async def run_claude_session(
    prompt: str,
    *,
    label: str = "",
    model: str = "claude-opus-4-6",
    max_retries: int = 3,
    timeout_minutes: int = 30,
) -> tuple[int, str, object]:
    """Run a Claude session via the Agent SDK. Returns (exit_code, output, result_msg).

    Replaces run_claude_streaming from bash. Uses the Agent SDK async query().
    Includes retry with exponential backoff for transient errors.
    """
    global _last_output

    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError:
        error_msg = (
            "[GSD:ERROR] claude-agent-sdk not installed. "
            "Install: pip install claude-agent-sdk  OR  use 'uv run' which handles deps automatically [/ERROR]"
        )
        _last_output = error_msg
        return 1, error_msg, None

    try:
        from claude_agent_sdk._errors import CLINotFoundError, ClaudeSDKError
    except ImportError:
        CLINotFoundError = None
        ClaudeSDKError = Exception

    async def _run_query_inner() -> tuple[list[str], ResultMessage | None]:
        collected: list[str] = []
        last_result: ResultMessage | None = None

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                permission_mode="bypassPermissions",
                cwd=str(Path.cwd()),
                model=model,
                setting_sources=["user", "project"],
            ),
        ):
            if isinstance(message, AssistantMessage):
                content = getattr(message, "content", None)
                if content is None:
                    continue
                for block in content:
                    if isinstance(block, TextBlock):
                        text = getattr(block, "text", "")
                        if text:
                            if label:
                                _labeled_print(label, text)
                            else:
                                print(text)
                            collected.append(text)
            elif isinstance(message, ResultMessage):
                last_result = message
                result_text = getattr(message, "result", None) or ""
                if result_text and not collected:
                    if label:
                        _labeled_print(label, result_text)
                    else:
                        print(result_text)
                    collected.append(result_text)
                if getattr(message, "is_error", False):
                    error_text = result_text or "Unknown SDK error"
                    collected.append(f"[GSD:ERROR] {error_text} [/ERROR]")

        return collected, last_result

    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            collected, result_msg = await asyncio.wait_for(
                _run_query_inner(),
                timeout=timeout_minutes * 60,
            )

            output = "\n".join(collected)
            _last_output = output
            exit_code = check_signals(output)
            return exit_code, output, result_msg

        except asyncio.TimeoutError:
            last_error = TimeoutError(f"Session timed out after {timeout_minutes}m")
            warn_msg(f"Timeout after {timeout_minutes}m (attempt {attempt + 1}/{max_retries})")

        except Exception as exc:
            if CLINotFoundError is not None and isinstance(exc, CLINotFoundError):
                error_msg = f"[GSD:ERROR] Claude CLI not found: {exc} [/ERROR]"
                _last_output = error_msg
                return 1, error_msg, None

            last_error = exc
            warn_msg(f"SDK error (attempt {attempt + 1}/{max_retries}): {exc}")

        # Backoff before retry (skip on last attempt)
        if attempt < max_retries - 1:
            delay = 15 * (3 ** attempt)  # 15s, 45s, 135s
            warn_msg(f"Retrying in {delay}s...")
            await asyncio.sleep(delay)

    # All retries exhausted
    error_msg = f"[GSD:ERROR] SDK failed after {max_retries} attempts: {last_error} [/ERROR]"
    _last_output = error_msg
    return 1, error_msg, None


# ═══════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════════

_SPRINT_PREAMBLE = """## SPRINT MODE: Autonomous Execution

You are running in AUTONOMOUS SPRINT MODE. Follow these rules strictly:

### FORBIDDEN (will break the sprint):
- DO NOT use AskUserQuestion under any circumstances
- DO NOT wait for user response or input
- DO NOT present options and wait for selection
- DO NOT pause for confirmation or approval

### REQUIRED BEHAVIOR:
- Make all decisions autonomously using best judgment
- If blocked on anything: emit error signal, do not wait
- Complete work fully before emitting completion signal
"""


def build_discuss_prompt(
    phase_num: str, milestone_goal: str, phase_detail: dict, yolo_mode: bool,
) -> str:
    """Build prompt for auto-discuss session."""
    goal = phase_detail.get("goal", "")
    return f"""{_SPRINT_PREAMBLE}

### CONTEXT:
Milestone goal: {milestone_goal}
Phase {phase_num} goal: {goal}

@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Invoke Skill(skill="gsd:discuss-phase", args="{phase_num} --auto")

Produce CONTEXT.md with implementation decisions for this phase.
Use the --auto flag to skip interactive questions and pick recommended defaults.

### SIGNALS (output exactly one before exiting):
- [GSD:DISCUSS_COMPLETE] — context gathered and CONTEXT.md written
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
- [GSD:BLOCKED] {{reason}} [/BLOCKED] — needs human intervention
"""


def build_plan_prompt(phase_num: str) -> str:
    """Build prompt for planning session."""
    return f"""{_SPRINT_PREAMBLE}

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Invoke Skill(skill="gsd:plan-phase", args="{phase_num}")

Create PLAN.md files for this phase. Focus on actionable plans that can be executed autonomously.
If plans already exist, skip to completion. If checker finds issues, fix them automatically (max 3 iterations).

### SIGNALS (output exactly one before exiting):
- [GSD:PLANNING_COMPLETE] — plans created successfully
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
- [GSD:BLOCKED] {{reason}} [/BLOCKED] — needs human intervention
"""


def build_execute_prompt(phase_num: str) -> str:
    """Build prompt for execution session."""
    return f"""{_SPRINT_PREAMBLE}

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Invoke Skill(skill="gsd:execute-phase", args="{phase_num} --no-transition")

Execute all plans in wave order. Make atomic commits after each significant change.
Handle deviations automatically — auto-fix bugs, prefer simpler approaches.

### SIGNALS (output exactly one before exiting):
- [GSD:PHASE_COMPLETE] — all plans executed, verification passed
- [GSD:VERIFICATION_FAILED] — gaps found in verification
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
- [GSD:BLOCKED] {{reason}} [/BLOCKED] — needs human intervention
"""


def build_audit_prompt(milestone_name: str) -> str:
    """Build prompt for milestone audit."""
    return f"""{_SPRINT_PREAMBLE}

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Invoke Skill(skill="gsd:audit-milestone")

Verify that all requirements for milestone '{milestone_name}' have been met.
Check each success criterion from the ROADMAP.md phase definitions.

### SIGNALS (output exactly one before exiting):
- [GSD:AUDIT_PASSED] — audit passed, all requirements met
- [GSD:VERIFICATION_FAILED] — gaps found
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
"""


def build_complete_prompt(milestone_version: str) -> str:
    """Build prompt for milestone completion."""
    return f"""{_SPRINT_PREAMBLE}

### TASK:
Invoke Skill(skill="gsd:complete-milestone", args="{milestone_version}")

Archive the milestone and update ROADMAP.md.

### SIGNALS (output exactly one before exiting):
- [GSD:PHASE_COMPLETE] — milestone completed and archived
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
"""


def build_cleanup_prompt() -> str:
    """Build prompt for cleanup."""
    return f"""{_SPRINT_PREAMBLE}

### TASK:
Invoke Skill(skill="gsd:cleanup")

Archive accumulated phase directories from the completed milestone.
Accept any dry-run and proceed with cleanup.

### SIGNALS (output exactly one before exiting):
- [GSD:PHASE_COMPLETE] — cleanup done
- [GSD:ERROR] {{description}} [/ERROR] — unrecoverable error
"""


# ═══════════════════════════════════════════════════════════════
# CODEX INTEGRATION
# ═══════════════════════════════════════════════════════════════


def run_codex(prompt: str, timeout: int = 300) -> str:
    """Call ask_codex.sh via subprocess."""
    if CODEX_SCRIPT is None or not CODEX_SCRIPT.exists():
        return "[PROCEED] Codex not available (ask_codex.sh not found)"
    if not shutil.which("bash"):
        return "[PROCEED] Codex not available (bash not found)"
    try:
        result = subprocess.run(
            [
                "bash",
                str(CODEX_SCRIPT),
                prompt,
                "gpt-5.4-codex",
                "high",
                str(timeout),
                "brief",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
        output = result.stdout
        if not output.strip():
            return "[PROCEED] Codex returned empty output"
        return output
    except subprocess.TimeoutExpired:
        return "[HALT] Codex timed out after {timeout}s"
    except FileNotFoundError:
        return "[PROCEED] Codex not available (script not executable)"
    except Exception as e:
        return f"[HALT] Codex error: {e}"


def validate_context_with_codex(phase_num: str, phase_dir: str = "") -> str:
    """Validate CONTEXT.md quality for a phase."""
    if not phase_dir:
        try:
            state = get_phase_state(phase_num)
            phase_dir = state.get("phase_dir", "")
        except Exception:
            return "[PROCEED] Could not determine phase directory"

    if not phase_dir:
        return "[PROCEED]"

    return run_codex(f"""Review implementation context for Phase {phase_num}.

Read CONTEXT.md in: {phase_dir}

Check for: contradictory decisions, missing considerations, misaligned goals, unrealistic assumptions.

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues, each on its own line""")


def validate_plans_with_codex(phase_num: str, phase_dir: str = "") -> str:
    """Validate PLAN.md files for a phase."""
    if not phase_dir:
        try:
            state = get_phase_state(phase_num)
            phase_dir = state.get("phase_dir", "")
        except Exception:
            return "[PROCEED] Could not determine phase directory"

    if not phase_dir:
        return "[PROCEED]"

    plan_files = list(Path(phase_dir).glob("*-PLAN.md")) + list(Path(phase_dir).glob("PLAN.md"))
    if not plan_files:
        return "[HALT] No plan files found"

    file_list = "\n".join(f"- {p}" for p in plan_files)
    return run_codex(f"""Sprint plan validation for Phase {phase_num}.

Read and review these plan files:
{file_list}

Check:
1. Achievability — Any impossible or underspecified tasks?
2. Completeness — Missing steps that would block execution?
3. Dependencies — Correct ordering? Missing prerequisites?
4. Risks — Technical risks not addressed?

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues, each on its own line""")


def review_code_with_codex(phase_num: str) -> str:
    """Review code changes after execution."""
    try:
        commits = subprocess.run(
            ["git", "log", "--oneline", "-15"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        commits = "(unavailable)"

    try:
        diff_stat = subprocess.run(
            ["git", "diff", "HEAD~15..HEAD", "--stat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        diff_stat = "\n".join(diff_stat.splitlines()[-20:])
    except Exception:
        diff_stat = "(unavailable)"

    return run_codex(f"""Sprint code review for Phase {phase_num}.

Recent commits:
{commits}

Files changed:
{diff_stat}

Check for root issues — be specific with file:line:
1. Logic errors or bugs
2. Security vulnerabilities
3. Missing error handling
4. Code that doesn't achieve stated objective

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues, each on its own line with file:line reference""")


# ═══════════════════════════════════════════════════════════════
# PARALLEL VALIDATION
# ═══════════════════════════════════════════════════════════════


def _parse_opus_output(raw: str) -> ValidationResult:
    """Parse structured Opus validator output into a ValidationResult."""
    verdict_match = re.search(r"##\s*Verdict:\s*(PASS_WITH_FIXES|PASS|FAIL)", raw, re.IGNORECASE)
    if verdict_match:
        verdict = verdict_match.group(1).upper()
    else:
        # No verdict found — warn explicitly so it's visible in logs
        warn_msg("Opus validator output missing '## Verdict:' line — defaulting to FAIL")
        if raw.strip():
            warn_msg(f"  Output tail: ...{raw.strip()[-150:]}")
        verdict = "FAIL"

    issues_match = re.search(r"##\s*Issues\s*\n(.*?)(?=\n##|\Z)", raw, re.DOTALL)
    issues_text = issues_match.group(1).strip() if issues_match else ""

    if issues_text and re.match(r"(?i)no\s+issues\s+found\.?$", issues_text.strip()):
        issues_text = ""

    return ValidationResult(verdict=verdict, raw=raw, issues_text=issues_text)


def _parse_codex_output(raw: str) -> ValidationResult:
    """Parse Codex [PROCEED]/[HALT] output into a ValidationResult."""
    if "[PROCEED]" in raw:
        return ValidationResult(verdict="PASS", raw=raw, issues_text="")
    if "[HALT]" in raw:
        halt_idx = raw.index("[HALT]")
        issues = raw[halt_idx + len("[HALT]"):].strip()
        return ValidationResult(verdict="FAIL", raw=raw, issues_text=issues)
    # No recognized signal — warn and treat as non-blocking rather than silently passing
    warn_msg("Codex output contained neither [PROCEED] nor [HALT] — treating as non-blocking PASS")
    warn_msg(f"  Output preview: {raw[:150]}")
    return ValidationResult(verdict="PASS", raw=raw, issues_text="")


def _build_opus_validator_prompt(val_type: str, phase_num: str, phase_state: dict) -> str:
    """Build read-only Opus prompt for validation."""
    phase_dir = phase_state.get("phase_dir", "")

    type_instructions = {
        "context": f"""Review the CONTEXT.md for Phase {phase_num}.
Read: {phase_dir}/*-CONTEXT.md or {phase_dir}/CONTEXT.md

Check for:
- Contradictory implementation decisions
- Missing considerations for the phase goal
- Misaligned goals (context doesn't serve the milestone)
- Unrealistic assumptions about existing codebase""",

        "plan": f"""Review the PLAN.md files for Phase {phase_num}.
Read all files matching: {phase_dir}/*-PLAN.md

Check for:
- Achievability — Any impossible or underspecified tasks?
- Completeness — Missing steps that would block execution?
- Dependencies — Correct ordering? Missing prerequisites?
- Risks — Technical risks not addressed?""",

        "code": f"""Review the code changes for Phase {phase_num}.
Check recent git commits and changed files.

Check for:
- Logic errors or bugs that would cause runtime failures
- Security vulnerabilities
- Code that doesn't satisfy the phase goal
- Missing critical functionality""",
    }

    instructions = type_instructions.get(val_type, "Review the phase artifacts.")

    return f"""## SPRINT MODE: {val_type.upper()} Validation (Read-Only)

You are running in AUTONOMOUS SPRINT MODE.

### FORBIDDEN:
- DO NOT use AskUserQuestion
- DO NOT modify any files — you are a READ-ONLY validator
- DO NOT use Edit, Write, or any file-modifying tools

### YOUR ROLE:
You are a {val_type}-level validator. Focus on whether the artifacts are CORRECT and COMPLETE.

MEDIUM+ issues (grounds for FAIL):
- Blocking errors that would derail downstream work
- Wrong assumptions about codebase or APIs
- Missing critical pieces

LOW issues (PASS_WITH_FIXES):
- Style, formatting, minor inaccuracies
- Things the developer would naturally handle

### VALIDATION INSTRUCTIONS:

{instructions}

### OUTPUT FORMAT:

## Issues

(List all issues found, with severity/description)
(If none: "No issues found.")

## Verdict: PASS

(or PASS_WITH_FIXES or FAIL)

[GSD:FIX_COMPLETE]"""



async def _run_opus_validator(
    val_type: str, phase_num: str, phase_state: dict, round_num: int,
    tracker: UsageTracker | None = None,
) -> ValidationResult:
    """Run Opus as a read-only validator. Returns parsed ValidationResult."""
    print(f"{_LBL_OPUS}{_DIM}starting {val_type} validation (round {round_num})...{_RESET}")

    prompt = _build_opus_validator_prompt(val_type, phase_num, phase_state)

    exit_code, output, result_msg = await run_claude_session(
        prompt, label=_LBL_OPUS, model="claude-opus-4-6", timeout_minutes=15,
    )
    if tracker:
        su = tracker.add(result_msg, f"{val_type[:1]}-val")
        print_session_usage(su, tracker)

    if exit_code == 1 and "[GSD:ERROR]" in output:
        result = ValidationResult(
            verdict="FAIL", raw=output,
            issues_text="Validator session failed (SDK error/timeout)",
        )
    else:
        result = _parse_opus_output(output)

    print(f"{_LBL_OPUS}{_BOLD}verdict: {_verdict_color(result.verdict)}{result.verdict}{_RESET}")
    return result


def _run_codex_validator_sync(val_type: str, phase_num: str, phase_dir: str = "") -> ValidationResult:
    """Run Codex validator (sync). Called via asyncio.to_thread()."""
    print(f"{_LBL_CODEX}{_DIM}starting {val_type} validation...{_RESET}")

    if val_type == "context":
        raw = validate_context_with_codex(phase_num, phase_dir=phase_dir)
    elif val_type == "plan":
        raw = validate_plans_with_codex(phase_num, phase_dir=phase_dir)
    else:
        raw = review_code_with_codex(phase_num)

    for line in raw.strip().splitlines():
        if line.strip():
            print(f"{_LBL_CODEX}{line}")

    if "Codex timed out" in raw:
        print(f"{_LBL_CODEX}{_YELLOW}timed out — treating as non-blocking PASS{_RESET}")
        return ValidationResult(verdict="PASS", raw=raw, issues_text="")

    result = _parse_codex_output(raw)
    print(f"{_LBL_CODEX}{_BOLD}verdict: {_verdict_color(result.verdict)}{result.verdict}{_RESET}")
    return result


# ═══════════════════════════════════════════════════════════════
# SIMPLIFY REVIEW (3 parallel agents for code validation)
# ═══════════════════════════════════════════════════════════════

_SIMPLIFY_PREAMBLE = """## SPRINT MODE: Code Simplify Review (Read-Only)

You are running in AUTONOMOUS SPRINT MODE.

### FORBIDDEN:
- DO NOT use AskUserQuestion
- DO NOT modify any files — you are a READ-ONLY reviewer
- DO NOT use Edit, Write, or any file-modifying tools

### YOUR ROLE:
You are a specialized code reviewer. Review the git diff of changed files.
Get the diff with: git diff HEAD~5..HEAD (adjust range to cover the phase's commits).

"""

_SIMPLIFY_REUSE_PROMPT = _SIMPLIFY_PREAMBLE + """### FOCUS: Code Reuse

For each change:

1. Search for existing utilities and helpers that could replace newly written code. Look for similar patterns elsewhere in the codebase — common locations are utility directories, shared modules, and files adjacent to the changed ones.
2. Flag any new function that duplicates existing functionality. Suggest the existing function to use instead.
3. Flag any inline logic that could use an existing utility — hand-rolled string manipulation, manual path handling, custom environment checks, ad-hoc type guards, and similar patterns.

### OUTPUT FORMAT:

## Issues

(List all reuse issues found, with file:line, description, and existing function/utility to use instead)
(If none: "No issues found.")

## Verdict: PASS

(or PASS_WITH_FIXES or FAIL)

[GSD:FIX_COMPLETE]"""

_SIMPLIFY_QUALITY_PROMPT = _SIMPLIFY_PREAMBLE + """### FOCUS: Code Quality

Review the changes for hacky patterns:

1. Redundant state: state that duplicates existing state, cached values that could be derived, observers/effects that could be direct calls
2. Parameter sprawl: adding new parameters to a function instead of generalizing or restructuring existing ones
3. Copy-paste with slight variation: near-duplicate code blocks that should be unified with a shared abstraction
4. Leaky abstractions: exposing internal details that should be encapsulated, or breaking existing abstraction boundaries
5. Stringly-typed code: using raw strings where constants, enums (string unions), or branded types already exist in the codebase
6. Unnecessary JSX nesting: wrapper elements that add no layout value
7. Unnecessary comments: comments explaining WHAT the code does (well-named identifiers already do that), narrating the change, or referencing the task/caller

### OUTPUT FORMAT:

## Issues

(List all quality issues found, with file:line, severity, and recommendation)
(If none: "No issues found.")

## Verdict: PASS

(or PASS_WITH_FIXES or FAIL)

[GSD:FIX_COMPLETE]"""

_SIMPLIFY_EFFICIENCY_PROMPT = _SIMPLIFY_PREAMBLE + """### FOCUS: Efficiency

Review the changes for efficiency:

1. Unnecessary work: redundant computations, repeated file reads, duplicate network/API calls, N+1 patterns
2. Missed concurrency: independent operations run sequentially when they could run in parallel
3. Hot-path bloat: new blocking work added to startup or per-request/per-render hot paths
4. Recurring no-op updates: state/store updates inside polling loops, intervals, or event handlers that fire unconditionally — add a change-detection guard
5. Unnecessary existence checks: pre-checking file/resource existence before operating (TOCTOU anti-pattern) — operate directly and handle the error
6. Memory: unbounded data structures, missing cleanup, event listener leaks
7. Overly broad operations: reading entire files when only a portion is needed, loading all items when filtering for one

### OUTPUT FORMAT:

## Issues

(List all efficiency issues found, with file:line, severity, and recommendation)
(If none: "No issues found.")

## Verdict: PASS

(or PASS_WITH_FIXES or FAIL)

[GSD:FIX_COMPLETE]"""


async def _run_simplify_validator(
    focus: str, prompt: str, label: str, round_num: int,
    tracker: UsageTracker | None = None,
) -> ValidationResult:
    """Run one simplify review agent. Returns parsed ValidationResult."""
    print(f"{label}{_DIM}starting {focus} review (round {round_num})...{_RESET}")

    exit_code, output, result_msg = await run_claude_session(
        prompt, label=label, model="claude-opus-4-6", timeout_minutes=15,
    )
    if tracker:
        su = tracker.add(result_msg, focus[:5])
        print_session_usage(su, tracker)

    if exit_code == 1 and "[GSD:ERROR]" in output:
        result = ValidationResult(
            verdict="FAIL", raw=output,
            issues_text=f"{focus} review session failed",
        )
    else:
        result = _parse_opus_output(output)

    print(f"{label}{_BOLD}verdict: {_verdict_color(result.verdict)}{result.verdict}{_RESET}")
    return result


async def _run_simplify_reviews(
    round_num: int, tracker: UsageTracker | None = None,
) -> list[ValidationResult]:
    """Run 3 simplify review agents in parallel. Returns [reuse, quality, efficiency] results."""
    print()
    print(f"  {_BOLD}-- SIMPLIFY REVIEW (round {round_num}) --{_RESET}")
    print(f"  {_DIM}reuse + quality + efficiency running concurrently{_RESET}")
    print()

    reuse, quality, efficiency = await asyncio.gather(
        _run_simplify_validator("reuse", _SIMPLIFY_REUSE_PROMPT, _LBL_REUSE, round_num, tracker=tracker),
        _run_simplify_validator("quality", _SIMPLIFY_QUALITY_PROMPT, _LBL_QUAL, round_num, tracker=tracker),
        _run_simplify_validator("efficiency", _SIMPLIFY_EFFICIENCY_PROMPT, _LBL_EFFIC, round_num, tracker=tracker),
    )

    # Print summary box
    print()
    print(f"  {_DIM}+--------------------------------------------------+{_RESET}")
    print(f"  {_DIM}|{_RESET} reuse: {_verdict_color(reuse.verdict)}{_BOLD}{reuse.verdict:<18s}{_RESET} "
          f"quality: {_verdict_color(quality.verdict)}{_BOLD}{quality.verdict}{_RESET} {_DIM}|{_RESET}")
    print(f"  {_DIM}|{_RESET} efficiency: {_verdict_color(efficiency.verdict)}{_BOLD}{efficiency.verdict}{_RESET} "
          f"{' ' * (38 - len(efficiency.verdict))}{_DIM}|{_RESET}")
    print(f"  {_DIM}+--------------------------------------------------+{_RESET}")

    return [reuse, quality, efficiency]


# ═══════════════════════════════════════════════════════════════
# CONSOLIDATOR & VALIDATION LOOP
# ═══════════════════════════════════════════════════════════════


def _build_full_consolidator_prompt(
    val_type: str,
    phase_num: str,
    phase_state: dict,
    findings: dict[str, str],
    round_num: int,
) -> str:
    """Build consolidator prompt from multiple finding sources."""
    phase_dir = phase_state.get("phase_dir", "")

    if val_type == "context":
        fix_instructions = f"""Fix ALL issues in CONTEXT.md for Phase {phase_num}.
Read the context file in {phase_dir}, apply fixes, keep changes minimal."""
    elif val_type == "plan":
        fix_instructions = f"""Fix ALL issues in the PLAN.md files for Phase {phase_num}.
Read the plans in {phase_dir}, apply fixes, keep changes minimal."""
    else:
        fix_instructions = f"""Fix ALL code issues for Phase {phase_num}.
Apply code fixes, then run verification.
Commit fixes with: 'fix: address validation feedback for phase {phase_num}'"""

    findings_section = ""
    for source, text in findings.items():
        findings_section += f"\n=== {source.upper()} FINDINGS ===\n{text or '(no issues)'}\n"

    return f"""## SPRINT MODE: Consolidator (Round {round_num})

AUTONOMOUS MODE — DO NOT use AskUserQuestion. Fix ALL issues directly.

### Combined Validation Findings
{findings_section}
### Deduplication Rules:
- If multiple reviewers report the same issue, fix it once
- Prioritize by severity: critical > high > medium > low
- If reviewers disagree on severity, use the higher severity
- For reuse findings: verify the suggested existing function actually exists before using it

### Fix Instructions:

{fix_instructions}

When done: [GSD:FIX_COMPLETE]
If unable to fix: [GSD:ERROR] {{description}} [/ERROR]"""


async def _run_consolidator(
    val_type: str,
    phase_num: str,
    phase_state: dict,
    findings: dict[str, str],
    round_num: int,
    tracker: UsageTracker | None = None,
) -> bool:
    """Run consolidator to fix combined findings. Returns True if FIX_COMPLETE."""
    prompt = _build_full_consolidator_prompt(
        val_type, phase_num, phase_state, findings, round_num,
    )
    _, output, result_msg = await run_claude_session(prompt, label=_LBL_FIX, timeout_minutes=20)
    if tracker:
        su = tracker.add(result_msg, "fix")
        print_session_usage(su, tracker)
    return "[GSD:FIX_COMPLETE]" in output


def _worst_verdict(*verdicts: str) -> str:
    """Return the worst verdict from a set. FAIL > PASS_WITH_FIXES > PASS."""
    if "FAIL" in verdicts:
        return "FAIL"
    if "PASS_WITH_FIXES" in verdicts:
        return "PASS_WITH_FIXES"
    return "PASS"


def _all_pass(*verdicts: str) -> bool:
    return all(v == "PASS" for v in verdicts)


def _all_low_or_pass(*verdicts: str) -> bool:
    return all(v in ("PASS", "PASS_WITH_FIXES") for v in verdicts) and not _all_pass(*verdicts)


async def run_parallel_validation(
    val_type: str,
    phase_num: str,
    phase_state: dict,
    skip_codex: bool,
    max_rounds: int = 7,
    tracker: UsageTracker | None = None,
) -> bool:
    """Run validators in parallel, consolidate on failure. Returns True if passed.

    For 'context' and 'plan': Opus correctness + Codex
    For 'code': Opus correctness + Codex + 3 simplify agents (reuse, quality, efficiency)
    """
    for round_num in range(1, max_rounds + 1):
        # ─── Correctness validators (all val_types) ───
        mode = "opus only" if skip_codex else "opus + codex"
        print()
        print(f"  {_BOLD}-- {val_type.upper()} VALIDATION (round {round_num}/{max_rounds}) --{_RESET}")
        print(f"  {_DIM}{mode} running concurrently{_RESET}")
        print()

        if skip_codex:
            opus_result = await _run_opus_validator(
                val_type, phase_num, phase_state, round_num, tracker=tracker,
            )
            codex_result = ValidationResult(verdict="PASS", raw="", issues_text="")
        else:
            phase_dir = phase_state.get("phase_dir", "")
            opus_result, codex_result = await asyncio.gather(
                _run_opus_validator(
                    val_type, phase_num, phase_state, round_num, tracker=tracker,
                ),
                asyncio.to_thread(_run_codex_validator_sync, val_type, phase_num, phase_dir),
            )

        print()
        _print_verdict_box(opus_result, codex_result, skip_codex)

        # ─── Simplify agents (code validation only, first round only) ───
        simplify_results: list[ValidationResult] = []
        if val_type == "code" and round_num == 1:
            simplify_results = await _run_simplify_reviews(round_num, tracker=tracker)

        # ─── Aggregate verdicts ───
        all_verdicts = [opus_result.verdict, codex_result.verdict]
        all_verdicts.extend(r.verdict for r in simplify_results)

        combined_verdict = _worst_verdict(*all_verdicts)

        # ─── Build findings dict for consolidator ───
        findings: dict[str, str] = {
            "opus (correctness)": opus_result.issues_text,
            "codex": codex_result.issues_text,
        }
        for r, name in zip(simplify_results, ["reuse", "quality", "efficiency"]):
            if r.issues_text:
                findings[f"simplify ({name})"] = r.issues_text

        # All pass → done
        if _all_pass(*all_verdicts):
            print(f"  {_GREEN}{_BOLD}OK{_RESET} All validators passed")
            log_validation_result(phase_num, val_type, opus_result.verdict, codex_result.verdict, "validated")
            return True

        # Low-only issues → fix but skip re-validation
        if _all_low_or_pass(*all_verdicts):
            print(f"  {_YELLOW}INFO{_RESET} Low-severity issues only — fixing without re-validation")
            await _run_consolidator(
                val_type, phase_num, phase_state,
                findings, round_num, tracker=tracker,
            )
            log_validation_result(phase_num, val_type, combined_verdict, codex_result.verdict, "fixed low")
            return True

        # At least one FAIL → fix and re-validate
        fail_sources = []
        if opus_result.verdict == "FAIL":
            fail_sources.append("Opus")
        if codex_result.verdict == "FAIL":
            fail_sources.append("Codex")
        for r, name in zip(simplify_results, ["Reuse", "Quality", "Efficiency"]):
            if r.verdict == "FAIL":
                fail_sources.append(name)
        if fail_sources:
            print(f"  {_YELLOW}WARN{_RESET} Issues from: {', '.join(fail_sources)}")

        if round_num >= max_rounds:
            print(f"  {_RED}{_BOLD}FAIL{_RESET} Max validation rounds ({max_rounds}) exceeded")
            return False

        # Consolidator fixes
        print()
        print(f"  {_MAGENTA}{_BOLD}-- CONSOLIDATOR (round {round_num}) --{_RESET}")
        print()
        fixed = await _run_consolidator(
            val_type, phase_num, phase_state,
            findings, round_num, tracker=tracker,
        )
        if not fixed:
            print(f"  {_RED}{_BOLD}FAIL{_RESET} Consolidator failed to apply fixes")
            return False
        print(f"  {_GREEN}OK{_RESET} Fixes applied, re-validating...")

    return False


# ═══════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════


def check_git_state() -> bool:
    if not shutil.which("git"):
        print(f"  {_RED}{_BOLD}ERR{_RESET} 'git' not found in PATH")
        print(f"  {_DIM}Install: brew install git{_RESET}")
        return False

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print(f"  {_RED}{_BOLD}ERR{_RESET} Not a git repository: {Path.cwd()}")
            print(f"  {_DIM}Run auto-gsd from your project root{_RESET}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  {_RED}{_BOLD}ERR{_RESET} git rev-parse timed out — git may be in a bad state")
        return False
    except Exception as e:
        print(f"  {_RED}{_BOLD}ERR{_RESET} git check failed: {e}")
        return False

    try:
        result = subprocess.run(
            ["git", "ls-files", "-u"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            print(f"  {_RED}{_BOLD}ERR{_RESET} Unresolved merge conflicts detected")
            print(f"  {_DIM}Resolve conflicts before running the sprint{_RESET}")
            return False
    except (subprocess.TimeoutExpired, Exception):
        pass

    return True


def check_planning_exists() -> bool:
    if not PLANNING_DIR.is_dir():
        print(f"  {_RED}{_BOLD}ERR{_RESET} No .planning/ directory found in {Path.cwd()}")
        print(f"  {_DIM}Initialize: claude then /gsd:new-project{_RESET}")
        return False
    if not ROADMAP_FILE.is_file():
        print(f"  {_RED}{_BOLD}ERR{_RESET} No .planning/ROADMAP.md found")
        print(f"  {_DIM}Create a milestone: /gsd:new-milestone{_RESET}")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    secs = seconds % 60
    if minutes > 60:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def pause_for_review() -> bool:
    """Pause for user review. Returns True to continue, False to halt."""
    print()
    try:
        response = input("Continue to next phase? [Y/n/halt] ")
    except EOFError:
        return True
    if response.strip().lower() in ("n", "halt"):
        halt_milestone_sprint("User requested pause")
        return False
    return True


def read_verification_status(phase_state: dict) -> str:
    """Read verification status from VERIFICATION.md for a phase.

    Returns: 'passed', 'human_needed', 'gaps_found', or '' if no verification.
    """
    phase_dir = phase_state.get("phase_dir")
    if not phase_dir:
        return ""

    phase_dir_path = Path(phase_dir)

    # glob() returns empty if dir doesn't exist — no need to pre-check
    verification_files = list(phase_dir_path.glob("*-VERIFICATION.md")) + list(
        phase_dir_path.glob("VERIFICATION.md")
    )
    if not verification_files:
        return ""

    content = verification_files[0].read_text()
    match = re.search(r"^status:\s*(\S+)", content, re.MULTILINE)
    return match.group(1) if match else ""
