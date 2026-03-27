# Milestone Completion Handler

Documents the lifecycle sequence run after all phases in a milestone are complete.

## Sequence

When all phases pass (or gaps are accepted), the sprint runs the lifecycle sequence:

### Step 1: Audit (`gsd:audit-milestone`)
- Verifies requirements coverage across all phases
- Checks cross-phase integration
- Validates end-to-end flows
- Routes on result: passed → continue, gaps_found → user decides

### Step 2: Complete (`gsd:complete-milestone`)
- Archives milestone to `.planning/milestones/`
- Updates ROADMAP.md to one-line summary with link
- Archives requirements
- Updates PROJECT.md with current state
- Creates git tag

### Step 3: Cleanup (`gsd:cleanup`)
- Archives accumulated phase directories
- Shows dry-run before proceeding

## State Tracking

The sprint loop updates MILESTONE-SPRINT.md throughout:

```yaml
status: running → complete
phases_completed: N
last_action: "Sprint completed successfully"
```

## Error Handling

If lifecycle steps fail:
- Audit failure with gaps → user prompted in interactive mode, accepted in yolo
- Complete failure → sprint halts, user can run manually
- Cleanup failure → non-fatal, user can run `/gsd:cleanup` later

## Cost Tracking

After lifecycle completes, the sprint banner shows:
- Total API cost across all phases and lifecycle steps
- Burn rate ($/hr)
- Weekly budget remaining
