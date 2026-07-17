---
name: sf-sprint-runner
description: Sequential ticket execution with push/PR/CI/merge and email reporting for sf-utils
argument-hint: "<DEV-123 DEV-124 | --cycle Backlog | --cycle 'In Progress'> [--dry-run] [--resume [path]]"
---

Batch-process multiple Jira tickets end-to-end: implement, push, PR, CI, merge, then move to the next ticket. Sends email reports on failure and at sprint completion.

**Scope**: This command extends sf-develop-ticket by adding post-commit phases (push, PR, CI wait, merge) and orchestrating multiple tickets sequentially with dependency resolution.

**Project Context**: Jira project key `DEV`, GitHub repo `antonajp/sf-utils`, base branch `main`.

## Phase 0: Input Parsing

Parse the argument string to determine which tickets to process:

### Mode A: Explicit Ticket List
If the argument does NOT start with `--cycle`, treat it as space-separated ticket IDs:
- Parse IDs (e.g., `DEV-123 DEV-124 DEV-125`)
- Validate each exists in Jira using `jira issue view`
- Reject if fewer than 1 or more than 20 tickets

### Mode B: Cycle/Status Query
If the argument starts with `--cycle`, extract the status name after it:
- Query Jira using jira-cli:
  ```
  jira issue list --project DEV --status "<status>" --plain --no-truncate --paginate 0:20
  ```
- Parse the output to extract ticket identifiers (first column contains ticket IDs like `DEV-123`)
- If zero tickets found, report and exit
- Present the resolved ticket list and ask for confirmation before proceeding

### Step 1: Parse Optional Flags

Parse flags left-to-right from the argument string before processing ticket IDs or `--cycle`:

1. If token matches `--dry-run`, set dry-run mode and remove the token
2. If token matches `--resume`:
   - If the next token exists AND does not start with `--` AND does not match `/^[A-Z0-9]+-[0-9]+$/`: treat it as the state file path and remove both tokens
   - Otherwise: use default path `~/.claude/sf-sprint-runner-state.json` and remove only the `--resume` token
3. After flag removal, remaining tokens are processed by Mode A, Mode B, or Mode R below

**Flag descriptions**:

- **`--dry-run`**: After dependency resolution (Phase 1), display the execution plan and exit without running any tickets.
- **`--resume [path]`**: Load sprint state from `path` (default: `~/.claude/sf-sprint-runner-state.json`). Skip tickets already completed in the previous run. The failed ticket is retried from the beginning (Step A). Remaining tickets proceed normally.

Both flags can be combined: `--dry-run --resume` loads the state file and displays what _would_ be executed on resume.

### Step 2: Determine Ticket Source

Check for `--resume` FIRST — it takes precedence over Mode A and Mode B:

- If `--resume` is active → proceed to **Mode R** (state file is the source of truth)
- Else if remaining args start with `--cycle` → proceed to **Mode B**
- Else → proceed to **Mode A**

### Mode R: Resume from State File (if `--resume`)

1. Load the state file from the parsed path (or default `~/.claude/sf-sprint-runner-state.json`)
2. If file is missing or invalid JSON, abort with a clear error
3. **Validate state file structure**:
   - `stateFileVersion` must equal `1`
   - `tickets`: required, array of strings, max length 20
   - `resolvedOrder`: required, array of strings, same length as `tickets`
   - `completed`: required, array of objects each with `id`, `title`, `prUrl`, `mergeSha`, `duration`
   - `failed`: optional, if present must have `id`, `title`, `step`, `reason`
   - `remaining`: required, array of strings
   - **All ticket IDs** in `tickets`, `resolvedOrder`, `completed[].id`, `failed.id`, and `remaining` MUST match `/^[A-Z0-9]+-[0-9]+$/`
   - If any validation fails, abort with a detailed error message
4. Use `resolvedOrder` from the state file as the execution order — skip Phase 1 dependency resolution entirely
5. Mark tickets in `completed` for skipping
6. The `failed` ticket (if any) will be retried from Step A

### Validation
- **Ticket ID format**: Every ticket ID MUST match `/^[A-Z0-9]+-[0-9]+$/` (e.g., `DEV-123`). Reject any non-conforming ID immediately.
- All tickets must belong to Jira project `DEV`.
- Cap at 20 tickets maximum (safety limit)
- Display the ticket list (ID, title, priority) before continuing

## Phase 1: Dependency Resolution

For each ticket in the set:

1. Run `jira issue view <TICKET-ID>` to get ticket details including linked issues
2. Extract `blockedBy` relationships from the "Blocks" / "Is blocked by" link types
3. Build a directed graph of dependencies:
   - Reject any ticket that blocks itself (malformed data) — abort with error
   - Only include edges where BOTH tickets are in the sprint set
4. **External blockers** (tickets NOT in the sprint set):
   - If the blocking ticket's status is Done → ignore (dependency satisfied)
   - If the blocking ticket is Cancelled → **prompt user**: "Ticket X is blocked by Y which is Cancelled. Proceed anyway?" If user declines, abort.
   - If the blocking ticket is in any other status → **abort the entire sprint** with an error listing the external blocker
5. **Topological sort** (Kahn's algorithm):
   - Initialize in-degree counts for all tickets in the set
   - Process tickets with zero in-degree first
   - If all tickets are processed → sorted order is the execution order
   - If tickets remain (cycle detected) → **abort** with the cycle path
6. Present the resolved execution order and confirm before proceeding

### Dry Run Exit (if `--dry-run`)

If `--dry-run` is active, display the execution plan and exit:

1. **Display execution plan** as a markdown table
2. **Time estimation heuristic**: Base estimate of 20 minutes per ticket.
3. **Exit** — do not proceed to Phase 2 or beyond.

## Phase 2: Pre-flight Checks

Run ALL of these checks. If any fail, abort with a clear error message:

1. **Clean working tree**:
   ```
   git status --porcelain
   ```
   Must produce empty output.

2. **On main branch, up-to-date**:
   ```
   git branch --show-current   # must be "main"
   git fetch origin main
   git diff HEAD origin/main --stat   # must be empty
   ```

3. **GitHub CLI authenticated**:
   ```
   gh auth status
   ```

4. **AWS CLI access** (for SES email):
   ```
   aws sts get-caller-identity --region us-east-2
   ```

5. **SES sender verification**:
   ```
   aws ses get-identity-verification-attributes --region us-east-2 --identities jp@iqaccel.com --query "VerificationAttributes.\"jp@iqaccel.com\".VerificationStatus" --output text
   ```
   Must return `Success`.

6. **jq available**:
   ```
   which jq
   ```

7. **jira-cli available and configured**:
   ```
   jira issue list --project DEV --paginate 0:1 --plain --no-headers
   ```

8. **Baseline tests pass**:
   ```
   source .venv/bin/activate && pytest tests/ -v --tb=short
   ```
   All tests must pass. If any fail, abort.

9. **GitHub API rate limit**: Estimate ~50 API calls per ticket. Warn if low.

10. **Record sprint start time**: Store for duration tracking.

## Phase 3: Sequential Execution Loop

For each ticket in the dependency-resolved order:

### Resume Skip Check (if `--resume`)

If `--resume` is active and the current ticket's ID appears in the state file's `completed` array, **skip** this ticket entirely.

### Main Divergence Check (after first ticket)

For every ticket **after the first**, check if `origin/main` has diverged:

```
git fetch origin main
git diff --quiet HEAD origin/main
```

Warn user if diverged; they can choose to continue or halt.

### Step A: Execute sf-develop-ticket Phases 0-7

Read the file `.claude/commands/sf-develop-ticket.md` and follow its Phase 0 through Phase 7 with these **overrides**:

- **Test runner**: Use `source .venv/bin/activate && pytest tests/ -v --tb=short`
- **Phase 6 commit**: Follow sf-develop-ticket's commit process exactly
- **Phase 7 Jira update**: Follow sf-develop-ticket's comment update but OVERRIDE status — do NOT set status to "In Review". Keep status as-is; sprint-runner manages status transitions in Step G after merge.

If Phase 4 (iteration) reaches 3 cycles without resolution → **fail this ticket** and proceed to Phase 4 (Failure Handling) below.

Record the ticket start time before Step A begins.

### Step B: Push Branch

```
git push -u origin <ticket-id>
```

### Step C: Create Pull Request

First check if a PR already exists for this branch:
```
gh pr list --head <ticket-id> --json number,url --jq '.[0]'
```
If a PR already exists, use that PR's number and URL. Skip creation.

If no PR exists, create one using HEREDOC for shell-safe body content:
```
gh pr create --base main --head <ticket-id> --title "feat(<ticket-id>): <brief description>" --body "$(cat <<'EOF'
## Summary
- <1-3 bullet points describing changes>

## Jira Ticket
[<ticket-id>: <ticket title>](https://improviselabs.atlassian.net/browse/<ticket-id>)

## Test plan
- [x] All unit tests passing (`pytest`)
- [x] Code review approved
- [x] Security audit passed

Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step D: Wait for CI

```
timeout 300 gh pr checks <PR-URL> --watch --fail-fast
```

- **Timeout (exit code 124)**: Fail this ticket.
- **No checks configured**: Proceed — local tests already passed.
- **Checks pass (exit code 0)**: Continue to Step E.
- **Checks fail (non-zero exit)**: Fail this ticket.

### Step E: Merge Pull Request

```
gh pr merge <PR-NUMBER> --merge --delete-branch
```

### Step F: Return to Main and Capture Merge SHA

```
git checkout main && git pull origin main
git log -1 --format='%H'
```

### Step G: Update Jira Ticket

Move the ticket status to **Done**:
```
jira issue move <ticket-id> "Done"
```

Add a comment to the ticket:
```
jira issue comment add <ticket-id> --body "**Merged via Sprint Runner**

**PR**: <PR-URL>
**Merge SHA**: <merge commit SHA>
**Duration**: <time for this ticket>"
```

### Step H: Record Result

Store for the sprint summary:
- Ticket ID, title
- Status: SUCCESS
- Duration, PR URL, Merge SHA, Iteration count

Then proceed to the next ticket.

## Phase 4: Failure Handling

Triggered when ANY step in Phase 3 fails:

1. **Write sprint state file** to `~/.claude/sf-sprint-runner-state.json` for future `--resume`

2. **Halt execution** — do not proceed to the next ticket

3. **Record failure** for the failed ticket

4. **Send failure notification email** via AWS SES using `jq` for safe JSON construction

5. **Preserve git state for investigation**: Return to main branch

6. **Add comment to failed ticket** in Jira

7. **Continue to Phase 5** (Sprint Summary)

## Phase 5: Sprint Summary

Always executed, whether the sprint completed fully or failed partway:

### Terminal Output

Display a markdown table:

```markdown
## Sprint Runner Summary

| # | Ticket | Title | Status | Duration | PR |
|---|--------|-------|--------|----------|-----|
| 1 | DEV-123 | Add bulk query | SUCCESS | 12m | #28 |
| 2 | DEV-124 | Error handling | FAILED | 8m | #29 |
| 3 | DEV-125 | Documentation | SKIPPED | — | — |

**Total Duration**: 20 minutes
**Result**: 1/3 tickets completed

### Sprint Metrics
| Metric | Value |
|--------|-------|
| Average time per ticket | 10m |
| Tickets completed per hour | 3.0 |
| Total iterations | 4 (across 2 tickets) |
```

### Summary Email

Send via AWS SES using `jq` for safe JSON construction.

**Subject line**:
- All succeeded: `sf-utils Sprint Complete: X/X tickets merged`
- Partial failure: `sf-utils Sprint FAILED: X/Y tickets merged`

### Next Steps Guidance

Report to the user:
- For **completed tickets**: No action needed, PRs merged and tickets marked Done
- For **failed ticket**: Branch preserved, investigate with `git checkout <ticket-id>`. After fixing, run `sf-sprint-runner --resume`
- For **skipped tickets**: Automatically included when using `--resume`

## Error Handling

- **Dirty working tree**: Abort before starting
- **Not on main**: Abort
- **GitHub CLI not authenticated**: Abort
- **AWS CLI not configured**: Abort
- **SES sender not verified**: Abort
- **jq not installed**: Abort
- **Baseline tests fail**: Abort
- **GitHub API rate limit low**: Warn user
- **External blocker incomplete**: Abort
- **Dependency cycle**: Abort with cycle path
- **Push fails**: Fail the ticket
- **PR creation fails**: Fail the ticket
- **CI timeout**: Fail the ticket after 5 minutes
- **Merge conflict**: Fail the ticket
- **Main branch diverged**: Warn user
- **SES email fails**: Log warning, don't abort
- **Implementation deadlock (3 iterations)**: Fail the ticket

## CLI Command Formatting

**IMPORTANT**: All CLI commands in Next Steps or output MUST be formatted as single-line commands for direct copy-paste execution.
