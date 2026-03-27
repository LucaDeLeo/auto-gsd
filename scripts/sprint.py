#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "claude-agent-sdk",
#     "pyyaml",
# ]
# ///
"""GSD Milestone Sprint — Phase-based autonomous execution with Codex validation.

Follows the bmad-sprint architecture: Python + Claude Agent SDK, parallel
Opus+Codex validation, 3-tier verdicts, usage tracking.

Usage:
    uv run sprint.py [milestone] [--interactive] [--skip-codex] [--resume] [--complete]

Examples:
    uv run sprint.py --yolo                    # Current milestone, AFK
    uv run sprint.py v1.2 --yolo               # Specific milestone
    uv run sprint.py --interactive              # Pause between phases
    uv run sprint.py --skip-codex              # No Codex validation
    uv run sprint.py --resume                  # Resume interrupted sprint
    uv run sprint.py --complete                # Auto-complete milestone
    uv run sprint.py --from-phase 5            # Start from phase 5
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

from sprint_signals import extract_error_details
from sprint_helpers import (
    MILESTONE_SPRINT_FILE,
    _BOLD,
    _DIM,
    _GREEN,
    _LBL_AUDIT,
    _LBL_CLEAN,
    _LBL_COMPL,
    _LBL_DISC,
    _LBL_EXEC,
    _LBL_PLAN,
    _RED,
    _RESET,
    _YELLOW,
    build_audit_prompt,
    build_cleanup_prompt,
    build_complete_prompt,
    build_discuss_prompt,
    build_execute_prompt,
    build_plan_prompt,
    check_no_active_milestone_sprint,
    create_checkpoint,
    dim_msg,
    discover_phases,
    finalize_milestone_sprint,
    format_duration,
    get_config_value,
    get_milestone_info,
    get_milestone_sprint_field,
    get_phase_detail,
    get_phase_state,
    get_stream_output,
    halt_milestone_sprint,
    init_milestone_sprint,
    is_infrastructure_phase,
    lifecycle_banner,
    load_milestone_sprint_state,
    log_milestone_phase_complete,
    ok_msg,
    pause_for_review,
    phase_banner,
    phase_complete_banner,
    print_preflight_result,
    read_verification_status,
    run_claude_session,
    run_parallel_validation,
    run_preflight_checks,
    session_banner,
    sprint_banner,
    sprint_complete_banner,
    update_milestone_sprint_field,
    warn_msg,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GSD Milestone Sprint: Phase-based autonomous execution with Codex validation",
    )
    parser.add_argument("milestone", nargs="?", default="", help="Milestone name (e.g., v1.2)")
    parser.add_argument("--interactive", action="store_true", help="Pause between phases (default: AFK/yolo mode)")
    parser.add_argument("--skip-codex", action="store_true", help="Skip Codex validation")
    parser.add_argument("--resume", action="store_true", help="Resume interrupted sprint")
    parser.add_argument("--complete", action="store_true", help="Auto-complete milestone when done")
    parser.add_argument("--force", action="store_true", help="Force restart, removing stale state")
    parser.add_argument("--from-phase", default="", help="Start from phase N")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    yolo_mode: bool = not args.interactive
    skip_codex: bool = args.skip_codex
    resume_mode: bool = args.resume
    auto_complete: bool = args.complete
    force_mode: bool = args.force
    from_phase: str = args.from_phase

    # ═══════════════════════════════════════════════════════════════
    # PRE-FLIGHT CHECKS
    # ═══════════════════════════════════════════════════════════════

    print(f"  {_DIM}Running preflight checks...{_RESET}")
    preflight = run_preflight_checks(skip_codex=skip_codex)
    print_preflight_result(preflight)

    if not preflight.ok:
        print(f"  {_RED}{_BOLD}Preflight failed — fix the errors above before running.{_RESET}")
        sys.exit(1)

    # ═══════════════════════════════════════════════════════════════
    # FORCE CLEANUP
    # ═══════════════════════════════════════════════════════════════

    if force_mode and resume_mode:
        print(f"  {_RED}ERROR{_RESET} --force and --resume are mutually exclusive")
        sys.exit(1)

    if force_mode and MILESTONE_SPRINT_FILE.exists():
        warn_msg("Removing stale sprint state (--force)")
        MILESTONE_SPRINT_FILE.unlink()

    # ═══════════════════════════════════════════════════════════════
    # INITIALIZE OR RESUME
    # ═══════════════════════════════════════════════════════════════

    milestone_version = ""
    milestone_name = ""

    if resume_mode:
        if not load_milestone_sprint_state():
            sys.exit(1)
        milestone_version = get_milestone_sprint_field("milestone")
        milestone_name = get_milestone_sprint_field("milestone_name")
        mode = get_milestone_sprint_field("mode")
        if mode == "yolo":
            yolo_mode = True
        auto_complete = get_milestone_sprint_field("auto_complete") == "true"
        from_phase = get_milestone_sprint_field("current_phase")
        print(f"Resuming from phase {from_phase}")
    else:
        if not check_no_active_milestone_sprint():
            sys.exit(1)

        # Get milestone info via gsd-tools
        try:
            milestone_info = get_milestone_info()
        except Exception as e:
            print(f"  {_RED}ERROR{_RESET} Failed to get milestone info: {e}")
            sys.exit(1)

        milestone_version = milestone_info.get("milestone_version", "")
        milestone_name = milestone_info.get("milestone_name", "")

        if not milestone_version:
            print(f"  {_RED}ERROR{_RESET} No current milestone found. Run /gsd:new-milestone first.")
            sys.exit(1)

    # Discover phases
    try:
        phase_queue = discover_phases(from_phase=from_phase)
    except Exception as e:
        print(f"  {_RED}ERROR{_RESET} Failed to discover phases: {e}")
        sys.exit(1)

    if not phase_queue:
        print("All phases complete. Nothing to do.")
        print(f"Run audit: /gsd:audit-milestone")
        sys.exit(0)

    phase_count = len(phase_queue)

    if not resume_mode:
        mode = "yolo" if yolo_mode else "interactive"
        init_milestone_sprint(milestone_name, milestone_version, phase_queue, mode, auto_complete)

    # ═══════════════════════════════════════════════════════════════
    # MAIN BANNER
    # ═══════════════════════════════════════════════════════════════

    sprint_banner(phase_count, yolo_mode, skip_codex, milestone_name, milestone_version, auto_complete)

    # ═══════════════════════════════════════════════════════════════
    # PHASE LOOP
    # ═══════════════════════════════════════════════════════════════

    completed_count = 0

    while phase_queue:
        phase = phase_queue.pop(0)
        completed_count += 1
        phase_index = completed_count

        phase_num = str(phase.get("number", ""))
        phase_name = phase.get("name", f"(unnamed phase {phase_num})")
        phase_goal = phase.get("goal", "")

        if not phase_num:
            warn_msg(f"Skipping phase with missing number: {phase}")
            continue

        phase_banner(phase_index, phase_count, phase_num, phase_name)
        update_milestone_sprint_field("current_phase", phase_num)
        update_milestone_sprint_field("status", "running")

        phase_start = int(time.time())

        # Get fresh phase state from gsd-tools
        try:
            phase_state = get_phase_state(phase_num)
        except Exception as e:
            warn_msg(f"Could not get phase state for phase {phase_num}: {e}")
            phase_state = {}

        try:
            phase_detail = get_phase_detail(phase_num)
        except Exception:
            phase_detail = {"goal": phase_goal}

        # ─────────────────────────────────────────────────────────
        # CONFIG CHECKS
        # ─────────────────────────────────────────────────────────

        skip_discuss = get_config_value("workflow.skip_discuss") == "true"
        is_infra = is_infrastructure_phase(phase_detail)

        # ─────────────────────────────────────────────────────────
        # DISCUSS
        # ─────────────────────────────────────────────────────────

        has_context = phase_state.get("has_context", False)

        if has_context:
            dim_msg(f"Context exists for phase {phase_num}, skipping discuss")
        elif skip_discuss or is_infra:
            reason = "config" if skip_discuss else "infrastructure"
            dim_msg(f"Discuss skipped ({reason}) — using ROADMAP phase goal as spec")
            # The plan-phase will handle missing context gracefully
        else:
            session_banner("DISCUSS")
            update_milestone_sprint_field("current_session", "discuss")
            create_checkpoint(phase_num, "discuss")

            prompt = build_discuss_prompt(
                phase_num,
                milestone_goal=f"{milestone_name} ({milestone_version})",
                phase_detail=phase_detail,
                yolo_mode=yolo_mode,
            )

            exit_code, output, result_msg = await run_claude_session(
                prompt, label=_LBL_DISC, timeout_minutes=20,
            )

            if exit_code == 0:
                print()
                ok_msg("Discuss complete")
            elif exit_code == 1:
                halt_milestone_sprint(f"Error in discuss for phase {phase_num}")
                details = extract_error_details(get_stream_output())
                if details:
                    warn_msg(details)
                sys.exit(1)
            elif exit_code == 3:
                halt_milestone_sprint(f"Phase {phase_num} discuss blocked — needs human")
                sys.exit(1)
            elif exit_code == 4:
                # No-signal recovery: check if CONTEXT.md was created
                try:
                    fresh_state = get_phase_state(phase_num)
                    if fresh_state.get("has_context"):
                        warn_msg("No signal but CONTEXT.md created — treating as success")
                    else:
                        halt_milestone_sprint(f"No signal from discuss for phase {phase_num}")
                        sys.exit(1)
                except Exception:
                    halt_milestone_sprint(f"No signal from discuss for phase {phase_num}")
                    sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # CONTEXT VALIDATION
        # ─────────────────────────────────────────────────────────

        if not skip_codex and not skip_discuss and not is_infra:
            # Re-fetch phase state (context may have just been created)
            try:
                phase_state = get_phase_state(phase_num)
            except Exception:
                pass

            if phase_state.get("has_context"):
                if not await run_parallel_validation(
                    "context", phase_num, phase_state, skip_codex,
                ):
                    halt_milestone_sprint(f"Context validation failed for phase {phase_num}")
                    sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # PLAN
        # ─────────────────────────────────────────────────────────

        # Re-fetch phase state
        try:
            phase_state = get_phase_state(phase_num)
        except Exception:
            pass

        has_plans = phase_state.get("has_plans", False)

        if has_plans:
            dim_msg(f"Plans exist for phase {phase_num}, skipping planning")
        else:
            session_banner("PLAN")
            update_milestone_sprint_field("current_session", "plan")
            create_checkpoint(phase_num, "plan")

            prompt = build_plan_prompt(phase_num)

            exit_code, output, result_msg = await run_claude_session(
                prompt, label=_LBL_PLAN, timeout_minutes=30,
            )

            if exit_code == 0:
                print()
                ok_msg("Planning complete")
            elif exit_code == 1:
                halt_milestone_sprint(f"Error in planning for phase {phase_num}")
                details = extract_error_details(get_stream_output())
                if details:
                    warn_msg(details)
                sys.exit(1)
            elif exit_code == 3:
                halt_milestone_sprint(f"Phase {phase_num} planning blocked")
                sys.exit(1)
            elif exit_code == 4:
                # No-signal recovery: check if PLAN.md files exist
                try:
                    fresh_state = get_phase_state(phase_num)
                    if fresh_state.get("has_plans"):
                        warn_msg("No signal but PLAN.md created — treating as success")
                    else:
                        halt_milestone_sprint(f"No signal from planning for phase {phase_num}")
                        sys.exit(1)
                except Exception:
                    halt_milestone_sprint(f"No signal from planning for phase {phase_num}")
                    sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # PLAN VALIDATION
        # ─────────────────────────────────────────────────────────

        if not skip_codex:
            try:
                phase_state = get_phase_state(phase_num)
            except Exception:
                pass

            if phase_state.get("has_plans"):
                if not await run_parallel_validation(
                    "plan", phase_num, phase_state, skip_codex,
                ):
                    halt_milestone_sprint(f"Plan validation failed for phase {phase_num}")
                    sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # EXECUTE
        # ─────────────────────────────────────────────────────────

        session_banner("EXECUTE")
        update_milestone_sprint_field("current_session", "execute")
        create_checkpoint(phase_num, "execute")

        prompt = build_execute_prompt(phase_num)

        exit_code, output, result_msg = await run_claude_session(
            prompt, label=_LBL_EXEC, timeout_minutes=60,
        )

        if exit_code == 0:
            print()
            ok_msg("Execution complete")
        elif exit_code == 1:
            halt_milestone_sprint(f"Error in execution for phase {phase_num}")
            details = extract_error_details(get_stream_output())
            if details:
                warn_msg(details)
            sys.exit(1)
        elif exit_code == 2:
            warn_msg(f"Verification failed for phase {phase_num}")
            # Continue — gaps will be detected below
        elif exit_code == 3:
            halt_milestone_sprint(f"Phase {phase_num} execution blocked")
            sys.exit(1)
        elif exit_code == 4:
            # No-signal recovery: check git changes + SUMMARY.md
            try:
                status_result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, timeout=10,
                )
                has_changes = bool(status_result.stdout.strip())
            except Exception:
                has_changes = False

            try:
                fresh_state = get_phase_state(phase_num)
                has_summaries = (fresh_state.get("summary_count", 0) or 0) > 0
            except Exception:
                has_summaries = False

            if has_changes or has_summaries:
                warn_msg("No signal but work detected — treating as success")
            else:
                halt_milestone_sprint(f"No signal from execution for phase {phase_num}")
                sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # CODE VALIDATION
        # ─────────────────────────────────────────────────────────

        if not skip_codex:
            try:
                phase_state = get_phase_state(phase_num)
            except Exception:
                pass

            if not await run_parallel_validation(
                "code", phase_num, phase_state, skip_codex,
            ):
                halt_milestone_sprint(f"Code validation failed for phase {phase_num}")
                sys.exit(1)

        # ─────────────────────────────────────────────────────────
        # VERIFICATION.md STATUS ROUTING
        # ─────────────────────────────────────────────────────────

        try:
            phase_state = get_phase_state(phase_num)
        except Exception:
            pass

        verify_status = read_verification_status(phase_state)

        if verify_status == "passed":
            ok_msg(f"Phase {phase_num} verification passed")
        elif verify_status == "human_needed":
            if yolo_mode:
                warn_msg(f"Phase {phase_num} has items needing human verification (deferred in yolo mode)")
            else:
                warn_msg(f"Phase {phase_num} has items needing human verification")
                # In interactive mode, user would review
        elif verify_status == "gaps_found":
            warn_msg(f"Phase {phase_num} verification found gaps")
            if yolo_mode:
                warn_msg("Continuing despite gaps (yolo mode)")
        elif verify_status:
            dim_msg(f"Verification status: {verify_status}")

        # ─────────────────────────────────────────────────────────
        # PHASE COMPLETE
        # ─────────────────────────────────────────────────────────

        phase_duration = int(time.time()) - phase_start
        duration_str = format_duration(phase_duration)
        codex_result = "skip" if skip_codex else "OK"

        log_milestone_phase_complete(phase_num, duration_str, codex_result, "")

        phase_complete_banner(phase_num, duration_str)

        # ─────────────────────────────────────────────────────────
        # RE-READ ROADMAP (catch inserted decimal phases)
        # ─────────────────────────────────────────────────────────

        try:
            refreshed = discover_phases(from_phase=phase_num)
            # Find phases in refreshed that aren't already in our remaining queue
            remaining_nums = {str(p.get("number", "")) for p in phase_queue}
            new_phases = [
                p for p in refreshed
                if str(p.get("number", "")) not in remaining_nums
                and str(p.get("number", "")) != phase_num
                and p.get("number") is not None
            ]
            if new_phases:
                warn_msg(f"Found {len(new_phases)} newly inserted phase(s)")
                # Merge new phases into queue maintaining sort order
                phase_queue.extend(new_phases)
                phase_queue.sort(key=lambda p: float(p.get("number", 0)))
                phase_count = completed_count + len(phase_queue)
        except Exception as e:
            dim_msg(f"Roadmap re-read failed (non-fatal): {e}")

        # ─────────────────────────────────────────────────────────
        # INTERACTIVE PAUSE
        # ─────────────────────────────────────────────────────────

        if not yolo_mode and phase_queue:
            if not pause_for_review():
                sys.exit(0)

    # ═══════════════════════════════════════════════════════════════
    # ALL PHASES COMPLETE — LIFECYCLE
    # ═══════════════════════════════════════════════════════════════

    lifecycle_banner(milestone_version, milestone_name)

    # ─── AUDIT ───

    session_banner("MILESTONE AUDIT")

    audit_start = int(time.time())
    prompt = build_audit_prompt(milestone_name)

    exit_code, output, result_msg = await run_claude_session(
        prompt, label=_LBL_AUDIT, timeout_minutes=30,
    )

    audit_duration = format_duration(int(time.time()) - audit_start)

    if exit_code == 0:
        ok_msg(f"Audit passed ({audit_duration})")
    elif exit_code == 2:
        warn_msg(f"Audit found gaps ({audit_duration})")
        if not yolo_mode:
            warn_msg("Fix gaps and re-run, or use --complete to accept")
    else:
        warn_msg(f"Audit completed with issues ({audit_duration})")

    # ─── COMPLETE (if --complete) ───

    if auto_complete:
        session_banner("COMPLETE MILESTONE")

        prompt = build_complete_prompt(milestone_version)
        exit_code, output, result_msg = await run_claude_session(
            prompt, label=_LBL_COMPL, timeout_minutes=15,
        )

        if exit_code == 0:
            ok_msg(f"Milestone '{milestone_name}' completed and archived")
        else:
            warn_msg("Milestone completion had issues")

        # Cleanup
        prompt = build_cleanup_prompt()
        exit_code, output, result_msg = await run_claude_session(
            prompt, label=_LBL_CLEAN, timeout_minutes=10,
        )

    else:
        print()
        print(f"  All phases executed and audit complete.")
        print(f"  To finalize: /gsd:complete-milestone {milestone_version}")
        print(f"  Or re-run with --complete to auto-finalize")

    # ═══════════════════════════════════════════════════════════════
    # SPRINT COMPLETE
    # ═══════════════════════════════════════════════════════════════

    finalize_milestone_sprint()

    sprint_complete_banner(completed_count)
    dim_msg(f"Sprint log: {MILESTONE_SPRINT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
