"""Signal detection for GSD Milestone Sprint — adapted from bmad-sprint pattern."""

import re


def check_signals(output: str) -> int:
    """Check output for GSD sprint signals. Returns exit code 0-4.

    0 = success (PHASE_COMPLETE, PLANNING_COMPLETE, DISCUSS_COMPLETE,
                 FIX_COMPLETE, AUDIT_PASSED)
    1 = error (ERROR)
    2 = verification/validation failed (VERIFICATION_FAILED)
    3 = blocked/checkpoint (BLOCKED, CHECKPOINT)
    4 = no signal found
    """
    # Fast path: if no GSD signal prefix at all, skip scanning
    if "[GSD:" not in output:
        return 4

    # Success signals (exit 0)
    for signal in (
        "[GSD:PHASE_COMPLETE]",
        "[GSD:PLANNING_COMPLETE]",
        "[GSD:DISCUSS_COMPLETE]",
        "[GSD:FIX_COMPLETE]",
        "[GSD:AUDIT_PASSED]",
    ):
        if signal in output:
            return 0

    # Error signal (exit 1)
    if "[GSD:ERROR]" in output:
        return 1

    # Verification/validation issues (exit 2)
    if "[GSD:VERIFICATION_FAILED]" in output:
        return 2

    # Blocked — needs human (exit 3)
    if "[GSD:BLOCKED]" in output:
        return 3
    if "[GSD:CHECKPOINT]" in output:
        return 3

    # No signal found (exit 4)
    return 4


def extract_error_details(output: str) -> str:
    """Extract text between [GSD:ERROR] and [/ERROR]."""
    match = re.search(r"\[GSD:ERROR\](.*?)\[/ERROR\]", output, re.DOTALL)
    if match:
        lines = match.group(1).strip().splitlines()
        return "\n".join(lines[:10])
    return ""
