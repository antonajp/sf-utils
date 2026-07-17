---
name: sf-develop-ticket
description: Retrieve a Jira ticket and implement it for sf-utils using parallel agent execution (pragmatic-shipper, api-design-reviewer, principal-code-reviewer, qa-quality-assurance, security-auditor) with iterative code review, security audit, and test verification
argument-hint: "<Jira ticket number>"
---

Retrieve a Jira ticket and implement its requirements for sf-utils using a coordinated team of five specialist agents. The command orchestrates parallel implementation followed by rigorous code review, API design review, security audit, and testing — iterating until all criteria are met.

**Target Platform**: Python 3.9+ library wrapping Salesforce REST APIs via SalesforcePy.

**Core Philosophy**:

  - **Ticket-Driven**: Implementation strictly follows ticket requirements and acceptance criteria.
  - **Iterative Refinement**: Code review feedback and testing drives improvement cycles.
  - **Agent Coordination**: Parallel execution with clear ownership boundaries.
  - **Review-Gated**: No implementation is complete without passing code review, API review, and security audit.
  - **Test-Verified**: All tests must pass before declaring done.

**Important**: This command implements code changes. It modifies the codebase based on ticket requirements. It does NOT push to remote — the user pushes manually after verification.

**Override Flags (optional)**:

  - `--nosec`: Skip security-auditor (use sparingly).
  - `--noapi`: Skip api-design-reviewer (internal-only changes).

## Core Agent Team

| Agent | Role in Development |
|-------|--------------------|
| **pragmatic-shipper** | Core implementation, business logic, module structure |
| **api-design-reviewer** | Public API design review, consistency checks, parameter patterns |
| **principal-code-reviewer** | Code quality, architecture review, Python best practices |
| **qa-quality-assurance** | Test creation, test execution, coverage validation |
| **security-auditor** | Security review, credential handling, SOQL injection prevention |

## Implementation Process

### Phase 0: Git Branch Setup

1. **Check current git status**: Ensure working tree is clean
2. **Create and checkout branch**: Branch name matches ticket ID (e.g., `DEV-123`)
   - If branch already exists locally, checkout existing branch
   - If branch doesn't exist, create new branch from current HEAD
3. **Record start time**: Begin tracking development time

### Phase 1: Ticket Retrieval & Analysis

1. **Retrieve ticket** from Jira using the jira-cli with the provided ticket number
2. **Parse ticket content**:
   - Extract business context and purpose
   - Identify acceptance criteria
   - Note technical specifications and constraints
   - Understand dependencies and risks
3. **Validate completeness**: Ensure ticket has sufficient detail for implementation. If critical information is missing, flag it and request clarification.

### Phase 2: Parallel Agent Implementation

Launch implementation agents in parallel (single message with multiple Task tool calls):

#### Implementation Agents

- **pragmatic-shipper**:
  - Implements core business logic and feature functionality
  - Creates new modules or extends existing ones
  - Ensures alignment with existing architectural patterns
  - Adds structured logging throughout for debugging
  - Follows the (body, status) tuple handling pattern for SalesforcePy responses

- **api-design-reviewer**:
  - Reviews proposed public API changes
  - Validates function signatures follow existing patterns
  - Checks parameter naming and ordering consistency
  - Ensures docstrings are complete and accurate
  - Validates return types are consistent

- **principal-code-reviewer**:
  - Monitors implementation progress
  - Identifies architectural concerns early
  - Prepares comprehensive code review checklist
  - Validates file size limits (600 lines max)

- **qa-quality-assurance**:
  - Creates unit tests with mocked SalesforcePy client
  - Tests edge cases: empty results, API errors, malformed responses
  - Tests error handling paths
  - Executes tests and reports results

- **security-auditor**:
  - Reviews credential handling in new code
  - Validates SOQL query construction (no injection)
  - Checks logging statements for credential leakage
  - Ensures error messages don't expose sensitive data
  - Prepares security findings report for review phase
  - **Claim verification**: Before reporting any finding, MUST verify the specific claim against the actual codebase using Glob/Grep/Read. Do NOT report findings based on assumed code patterns.

#### Agent Coordination Rules

- Each agent operates on clearly separated concerns to avoid file conflicts
- pragmatic-shipper owns core implementation in sf_utils/
- qa-quality-assurance owns test files in tests/
- Shared files require explicit coordination between agents
- All agents follow project rules (600-line file limit, modular design, no hardcoded secrets)

#### Jira Ticket Update: Implementation Complete

After parallel implementation, add a comment to the Jira ticket:

```markdown
**Implementation Complete - Iteration 1**

**Files Modified**:
- List of files changed by each agent

**Key Changes**:
- Core Logic: Summary of pragmatic-shipper changes
- API Design: Summary of api-design-reviewer findings
- Security: Summary of security-auditor preliminary findings

**Status**: Ready for code review and security audit
```

### Phase 3: Code Review & Security Audit

The **principal-code-reviewer**, **api-design-reviewer**, and **security-auditor** agents perform comprehensive review:

#### Review Criteria

1. **Functional Completeness**
   - All acceptance criteria from ticket are met
   - Edge cases are handled appropriately
   - Error handling is robust and production-ready

2. **API Design Quality**
   - Function signatures follow existing patterns
   - Parameter naming is consistent with other functions
   - Return types match existing conventions
   - Docstrings are complete (Args, Returns, Raises)

3. **Architectural Integrity**
   - Code follows existing patterns and conventions
   - Separation of concerns is maintained
   - No unnecessary complexity or feature bloat
   - Files respect 600-line limit

4. **Code Quality**
   - KISS and DRY principles followed
   - Structured logging with level controls
   - Code is self-explanatory
   - Type hints on public functions

5. **Security** (from security-auditor)
   - Credentials never logged, even at DEBUG level
   - SOQL queries properly constructed (no injection)
   - Error messages don't leak sensitive information
   - API tokens handled securely
   - CRITICAL/HIGH security issues must be addressed before approval

6. **Testing & Stability**
   - Unit tests cover all public functions
   - Edge cases tested (empty results, errors, malformed data)
   - Mocking patterns are correct
   - No regressions introduced

#### Review Output Format

```markdown
## Code Review Summary

**Review Status**: [APPROVED | CHANGES REQUESTED]
**API Design Status**: [APPROVED | CHANGES REQUESTED]
**Security Status**: [PASSED | ISSUES FOUND]
**Iteration**: [1, 2, 3...]

### Strengths
- List what was well-implemented
- Highlight adherence to project principles

### Issues Found
[Only if CHANGES REQUESTED]

#### Critical Issues (Must Fix)
- Issue description with file location and line numbers
- Specific remediation required

#### Suggestions (Should Fix)
- Improvement opportunities
- Refactoring recommendations

### API Design Review
**Consistency**: [Consistent with existing API | Issues found]
**Documentation**: [Complete | Gaps identified]
**Developer Experience**: [Good | Improvements suggested]

### Security Audit Results
**Findings**: [X Critical | Y High | Z Medium | W Low]

[If any CRITICAL or HIGH findings]:
#### Security Issues (Must Fix)
- [Severity] Issue description (CWE/OWASP reference)
- Location: file:line
- Remediation: Specific fix required

[If only MEDIUM or LOW findings]:
#### Security Recommendations (Follow-up Tickets)
- [Severity] Brief description for ticket creation

### Acceptance Criteria Status
- [ ] Criterion 1: Status and notes
- [ ] Criterion 2: Status and notes
- [ ] Criterion 3: Status and notes

### Next Steps
[If APPROVED]: Ready for local testing
[If CHANGES REQUESTED]: Specific actions for next iteration
```

#### Jira Ticket Update: Code Review Complete

After code review and security audit, add a comment to the Jira ticket with the review status, findings summary, and next steps.

### Phase 4: Iteration (If Needed)

If code review, API review, security audit, or testing finds issues requiring changes:

1. **Targeted agent dispatch**: Only agents responsible for flagged issues are re-invoked
2. **Focused scope**: Agents receive specific review feedback to address
3. **Surgical fixes**: Minimal, targeted changes to resolve review items
4. **Re-review**: principal-code-reviewer, api-design-reviewer, security-auditor, and qa-quality-assurance evaluate fixes

**Iteration continues** until:
- Code review status is APPROVED
- API design review is APPROVED
- Security audit has no CRITICAL/HIGH findings
- All tests are passing

### Phase 5: Local Testing & Verification

Once code review is APPROVED, verify locally:

1. **Run all tests**:
   ```bash
   source .venv/bin/activate && pytest tests/ -v --tb=short
   ```

2. **Run type checking** (if mypy configured):
   ```bash
   mypy sf_utils/
   ```

3. **Verify imports work**:
   ```bash
   python -c "from sf_utils import *; print('Imports OK')"
   ```

4. **Fix any issues** found during local verification — return to Phase 4 if needed

### Phase 6: Commit

Once local verification passes:

1. **Git commit**: Commit all changes with generated commit message
   - Stage modified files by name (not `git add -A`)
   - Use conventional commits format: `feat(<ticket-id>): Brief description`
   - Include ticket ID in commit message
   - Include detailed change list in commit body
2. **Do NOT push** — the user pushes manually after their own verification

### Phase 7: Jira Ticket Update & Completion

1. **Calculate development time**: From Phase 0 start to Phase 6 completion (rounded to nearest 0.25 hours)

2. **Update Jira ticket** with final summary:

```markdown
**Implementation Complete**

**Branch**: <ticket-id>
**Development Time**: Y hours
**Total Iterations**: X

**Summary**:
- All acceptance criteria met
- X files modified
- Code review approved
- API design review approved
- Security audit passed
- All tests passing

**Files Changed**:
- `sf_utils/new_module.py`: Description
- `tests/test_new_module.py`: Description

**Local Verification Results**:
- Tests: All passing (X tests)
- Type checking: Passed
- Imports: OK

**Next Steps**:
1. User verifies locally
2. User pushes branch: `git push origin <branch>`
3. User creates PR
```

3. Leave the ticket in **In Review** status. Only the user can decide if ticket is Done.

## Agent Execution Pattern

```
Phase 0: GIT SETUP
├─ Check git status (clean working tree)
├─ Create/checkout branch matching ticket ID
└─ Record start time

Phase 1: RETRIEVE
├─ jira-cli → Fetch ticket
└─ Parse & validate requirements

Phase 2: IMPLEMENT (Parallel)
├─ pragmatic-shipper → Core implementation
├─ api-design-reviewer → API design validation
├─ principal-code-reviewer → Prepare review checklist
├─ qa-quality-assurance → Create and run tests
└─ security-auditor → Initial security review

Phase 3: REVIEW & AUDIT (Parallel)
├─ principal-code-reviewer → Comprehensive code review
│   ├─ APPROVED → Check API design
│   └─ CHANGES REQUESTED → Phase 4
├─ api-design-reviewer → API consistency check
│   ├─ APPROVED → Check security
│   └─ CHANGES REQUESTED → Phase 4
├─ security-auditor → Security audit
│   ├─ PASSED (no CRITICAL/HIGH) → Check tests
│   └─ ISSUES FOUND (CRITICAL/HIGH) → Phase 4
└─ qa-quality-assurance → Run all tests
    ├─ ALL TESTS PASS → Phase 5
    └─ TEST FAILURES → Phase 4

Phase 4: ITERATE (Conditional)
├─ Dispatch targeted agents based on review feedback
├─ Surgical fixes applied
└─ Loop to Phase 3

Phase 5: LOCAL TESTING & VERIFICATION
├─ Run pytest
├─ Run mypy (if configured)
├─ Verify imports
└─ On failure → Phase 4

Phase 6: COMMIT
├─ Stage specific files
├─ Git commit with conventional message
└─ Do NOT push (user pushes manually)

Phase 7: UPDATE & COMPLETE
├─ Calculate development time
├─ Update Jira ticket with summary
└─ Output final report with next steps
```

## Error Handling

- **Dirty working tree**: Report uncommitted changes and request user to commit or stash before proceeding
- **Branch already exists with conflicts**: Report conflict and ask user to resolve manually
- **Ticket not found**: Report error and exit
- **Incomplete ticket**: List missing information and request user guidance
- **Agent conflicts**: Detect file collision and serialize conflicting changes
- **Review deadlock**: After 3 iterations without approval, summarize blocking issues and request user intervention
- **Test failures**: Report failures with context, dispatch targeted agents to fix

## Output Format

```markdown
## Ticket Implementation: <TICKET-ID>

**Ticket Title**: <Title from Jira>
**Status**: [VERIFIED LOCALLY | IN PROGRESS | BLOCKED]
**Branch**: <ticket-id>
**Code Iterations**: X
**Development Time**: Y hours

### Summary
Brief description of what was implemented

### Acceptance Criteria Met
- [x] Criterion 1
- [x] Criterion 2
- [x] Criterion 3

### API Design Summary
**New Public Functions**: X
**Consistency**: Follows existing patterns
**Documentation**: Complete

### Security Audit Summary
**Status**: PASSED
**Findings**: 0 Critical | 0 High | X Medium | Y Low
**Follow-up Tickets**: [If any LOW/MEDIUM findings deferred]

### Files Modified
- `sf_utils/new_module.py` - New functionality
- `sf_utils/__init__.py` - Export new functions
- `tests/test_new_module.py` - Unit tests

### Local Verification Results
| Check | Status | Details |
|-------|--------|---------|
| Tests | Passed | 12/12 passing |
| Type Check | Passed | No errors |
| Imports | OK | All exports work |

### Jira Ticket Updates
- X comments added throughout the process
- Development time tracked and updated on ticket

### Next Steps
1. Verify locally
2. Push branch: `git push origin <ticket-id>`
3. Create PR
```

## CLI Command Formatting

**IMPORTANT**: All CLI commands presented in the Next Steps or anywhere else in the output MUST be formatted as single-line commands. Single-line commands enable direct copy-paste execution from the terminal.
