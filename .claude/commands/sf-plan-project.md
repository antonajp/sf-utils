---
name: sf-plan-project
description: Transform a detailed project idea into a comprehensive user story work breakdown structure with sprint plan, using interactive Q&A and the sf-utils specialist agents
argument-hint: "<detailed project idea description>"
---

Transform a high-level project idea into a comprehensive agile user story work breakdown structure (WBS) with epics, user stories, story points, sprint assignments, and dependency maps. The output is a markdown document written to the target project's `docs/` directory.

**Required argument**: `$ARGUMENTS` is a detailed description of the project idea.

**Assumptions**:
- The `CLAUDE.md` exists in the current working directory with project objectives, git repo, and safety constraints.
- The command will interactively ask the user follow-up questions to gather all necessary context before generating the plan.

**Methodology**: Agile/Scrum with 2-week sprints, Fibonacci story point scale (1, 2, 3, 5, 8, 13).

## Phase 0: Context Discovery

1. **Read existing CLAUDE.md** in the current working directory:
   - Extract: project objectives, git repo, team name, Jira project (DEV), technology choices
   - Note any naming conventions or architectural patterns already established

2. **Parse the project idea** from `$ARGUMENTS`:
   - Identify the core problem being solved
   - Identify the target users/audience (library developers using Salesforce)
   - Identify any explicitly stated Salesforce API features
   - Identify scope boundaries (what's in vs. out)

3. **Scan the working directory** for existing project structure:
   - Check for `pyproject.toml`, `requirements.txt` to detect existing dependencies
   - Check for existing `sf_utils/` modules
   - Check for existing `tests/` structure
   - Check for existing `docs/` directory
   - Record findings to avoid asking questions the codebase already answers

## Phase 1: Interactive Q&A

Ask the user follow-up questions using `AskUserQuestion` to fill in gaps not answered by the project idea or CLAUDE.md. Ask questions in batches of 2-4 (the tool maximum) to minimize round trips. Skip questions where the answer is already clear from Phase 0 context.

### Batch 1: Salesforce API Scope

Ask up to 4 of the following (skip any already answered):

- **Salesforce APIs**: Which Salesforce APIs should this feature/module support?
  - Options: REST API (current), Bulk API, Tooling API, Metadata API, Composite API
  - This determines the scope of new modules

- **Object Types**: What Salesforce object types are in scope?
  - Options: Standard objects only, Custom objects, Both, Any object

- **Operations**: What operations are needed?
  - Multi-select. Options: Query (SOQL), CRUD (create/read/update/delete), Bulk operations, Metadata operations

- **Authentication**: Any new authentication methods needed?
  - Options: Use existing (username/password OAuth), Add JWT bearer flow, Add connected app flow

### Batch 2: Developer Experience

Ask up to 4 of the following:

- **Error Handling**: What error handling approach?
  - Options: Custom exceptions (recommended), Built-in exceptions, Error codes

- **Async Support**: Is async/await support needed?
  - Options: Sync only (current), Add async variants, Async only

- **Pagination**: How should large result sets be handled?
  - Options: Automatic pagination (current query_all), Manual pagination, Streaming/generators

- **Logging**: What logging level is appropriate?
  - Options: Debug-heavy (current), Minimal, Configurable

### Batch 3: Planning Parameters

Ask up to 4 of the following:

- **Team Size**: How many engineers will work on this?
  - Options: Solo (1), Small team (2-3)
  - This affects sprint velocity estimates

- **MVP Scope**: What is the MVP vs. future scope boundary?
  - Let the user describe what constitutes the minimum viable product

- **Timeline Pressure**: What is the timeline expectation?
  - Options: ASAP / aggressive, Normal pace, Flexible / no deadline

- **Breaking Changes**: Are breaking changes acceptable?
  - Options: No breaking changes, Minor breaks OK with deprecation, Major version bump acceptable

### Adaptive Follow-ups

Based on answers received, ask additional targeted questions:

- If **Bulk API** selected: Batch size limits? Async job monitoring?
- If **Async support** selected: Which functions need async variants?
- If **Custom exceptions** selected: What exception hierarchy?

Continue asking until all critical architectural decisions have answers. Maximum 2 additional batches of follow-ups.

## Phase 2: Agent Selection & Research

Based on Phase 1 answers, select and launch the appropriate specialist agents in parallel. Each agent receives the full project context.

### Agent Selection Matrix

| Condition | Agent | Role |
|-----------|-------|------|
| **Always** | `prd-writer` | Epics, user stories, acceptance criteria, priority assignments, story dependency map |
| **Always** | `pragmatic-shipper` | Implementation sequencing, effort estimation, risk identification, sprint assignment |
| **Always** | `api-design-reviewer` | API design for each epic, function signatures, consistency requirements, developer experience |
| **Always** | `security-auditor` | Security requirements per epic, credential handling stories, SOQL injection prevention |
| **Always** | `qa-quality-assurance` | Test strategy per epic, testing stories, mocking approach, quality gates |
| **Always** | `principal-code-reviewer` | Code quality standards, module organization, file structure recommendations |

### Agent Prompt Template

Each agent receives:

```
You are contributing to a project planning exercise for sf-utils, a Python library wrapping Salesforce REST APIs. Your role is <agent role>.

PROJECT IDEA:
<$ARGUMENTS>

PROJECT CONTEXT:
- Language: Python 3.9+
- Salesforce Library: SalesforcePy 2.2+
- Testing: pytest with mocked responses
- Salesforce APIs: <answer>
- Object Types: <answer>
- Operations: <answer>
- Error Handling: <answer>
- Async Support: <answer>
- Team Size: <answer>
- MVP Scope: <answer>
- Breaking Changes: <answer>

EXISTING CODEBASE CONTEXT:
<findings from Phase 0 directory scan>
- Current modules: client.py, query.py, sobjects.py
- Current public functions: get_client, query, query_all, get_record, create_record, update_record, upsert_record, delete_record, describe_object

YOUR TASK:
Based on your area of expertise, provide:

1. **Recommended Epics**: List the epics that fall within your domain. For each epic:
   - Epic title and description
   - Why this epic is needed
   - Sprint recommendation (which sprint it belongs to)

2. **User Stories**: For each epic, provide detailed user stories following this format:
   - Story ID suggestion (e.g., US-XXX)
   - Story title
   - "As a <role>, I want to <action> so that <benefit>"
   - Acceptance criteria (checkboxes)
   - Story points (Fibonacci: 1, 2, 3, 5, 8, 13)
   - Priority: Must Have, Should Have, or Nice to Have
   - Sprint assignment
   - Dependencies on other stories (if any)

3. **Technical Notes**: Any technical considerations, assumptions, or constraints relevant to your domain.

4. **Risks**: Key risks in your domain area and suggested mitigations.

Use Fibonacci story points: 1 (trivial), 2 (small), 3 (medium), 5 (large), 8 (very large), 13 (epic-sized, consider splitting).
Assume 2-week sprints with team velocity of <calculated based on team size> points per sprint.
```

**Team velocity calculation**:
- Solo (1): ~15-20 pts/sprint
- Small team (2-3): ~25-35 pts/sprint

### Launching Agents

Launch ALL selected agents in parallel using a single message with multiple Task tool calls.

## Phase 3: Synthesis

After all agents return, synthesize their outputs into a unified plan:

1. **Deduplicate**: Multiple agents may suggest overlapping stories. Merge duplicates, keeping the more detailed version.

2. **Assign Story IDs**: Number stories sequentially (US-001, US-002, ...) across all epics.

3. **Resolve Dependencies**: Build the dependency graph across all agent contributions. Ensure no circular dependencies. Adjust sprint assignments if dependencies require reordering.

4. **Validate Sprint Loading**: Ensure no sprint exceeds the team's estimated velocity. Rebalance if needed.

5. **Priority Calibration**: Ensure Must Have stories form a coherent MVP path. Should Have and Nice to Have stories should be in later sprints.

6. **Calculate Summary Metrics**: Total epics, total stories, total story points, breakdown by priority, estimated duration.

## Phase 4: Document Generation

Write the user story WBS document to `<project-root>/docs/sf-utils-user-stories.md`.

If the `docs/` directory does not exist, create it.

### Document Structure

```markdown
# sf-utils

## Agile User Stories & Sprint Plan

**Prepared:** <current date> · **Platform:** Python/PyPI · **Methodology:** Agile/Scrum

---

## Sprint Roadmap Overview

| Sprint | Focus Area | Key Deliverables |
|---|---|---|
| **Sprint 1** (Weeks 1–2) | <focus> | <deliverables> |
| **Sprint 2** (Weeks 3–4) | <focus> | <deliverables> |
| ... | ... | ... |

---

## Summary Metrics

| Metric | Value |
|---|---|
| Total Epics | <count> |
| Total User Stories | <count> |
| Total Story Points | <sum> |
| Must Have Stories | <count> |
| Should Have Stories | <count> |
| Nice to Have Stories | <count> |
| Estimated Duration | <X> core sprints (<Y> weeks) + future backlog |

---

## EPIC-01 — <Epic Title>

> <Epic description>

| ID | Story Title | Points | Priority | Sprint |
|---|---|---|---|---|
| US-001 | <title> | <points> | **<priority>** | Sprint <N> |
| US-002 | <title> | <points> | **<priority>** | Sprint <N> |

### US-001: <Story Title>

| | |
|---|---|
| **Points** | <N> |
| **Priority** | <emoji> <priority> |
| **Sprint** | Sprint <N> |

**As a** <role>, **I want to** <action> **so that** <benefit>.

**Acceptance Criteria:**

- ✓ <criterion 1>
- ✓ <criterion 2>
- ✓ <criterion 3>

---

<repeat for each story in the epic>

<repeat for each epic>

---

## Story Dependency Map

| Dependency Chain | Rationale |
|---|---|
| **US-001 → US-002 → US-004** | <rationale> |
| ... | ... |

---

## Technical Notes & Assumptions

| Topic | Detail |
|---|---|
| **Sprint Duration** | 2 weeks per sprint, assuming <team size> team |
| **Story Points** | Fibonacci scale (1, 2, 3, 5, 8, 13). Team velocity estimated at ~<N> pts/sprint. |
| **SalesforcePy Dependency** | All implementations build on SalesforcePy 2.2+ |
| **Testing Approach** | pytest with mocked SalesforcePy client responses |
| **<topic>** | <detail> |
| ... | ... |

---

## API Design Principles

> Guidelines from api-design-reviewer for maintaining consistency

- **Function Signatures**: Required positional params, optional keyword params, `client` always last
- **Return Types**: Consistent with existing functions (List[Dict], Dict, str, bool)
- **Error Handling**: <chosen approach>
- **Docstrings**: Google style with Args, Returns, Raises sections
```

**Priority emoji mapping**:
- Must Have: 🔴
- Should Have: 🟠
- Nice to Have: 🟢

## Phase 5: Output & Confirmation

1. **Write the user stories document** to `docs/sf-utils-user-stories.md`
2. **Display a summary** to the terminal:

```markdown
## Project Plan Generated

**Document**: docs/sf-utils-user-stories.md

### Plan Summary
| Metric | Value |
|--------|-------|
| Epics | <N> |
| User Stories | <N> |
| Story Points | <N> |
| Sprints | <N> core + backlog |
| Agents Consulted | prd-writer, pragmatic-shipper, api-design-reviewer, security-auditor, qa-quality-assurance, principal-code-reviewer |

### Next Steps
1. Review the user stories document at `docs/sf-utils-user-stories.md`
2. Adjust story points, priorities, and sprint assignments as needed
3. Create Jira tickets from the user stories using `/sf-create-ticket` or manually
4. Begin Sprint 1 development with `/sf-develop-ticket`
```

## Error Handling

- **No project idea provided**: Prompt the user to describe their project idea
- **No writable directory**: Report error if docs/ cannot be created
- **Agent timeout**: Report partial findings, note which domains were not covered
- **Insufficient detail**: If the project idea is too vague to generate meaningful stories even after Q&A, report what's missing and ask for more detail
- **Story point overflow**: If a single story exceeds 13 points, automatically suggest splitting it into sub-stories

## Usage Examples

```bash
# Plan adding Bulk API support
/sf-plan-project Add support for Salesforce Bulk API 2.0 with async job monitoring, CSV/JSON data formats, and automatic chunking for large datasets

# Plan error handling improvements
/sf-plan-project Implement a comprehensive exception hierarchy with specific exceptions for auth failures, API errors, rate limits, and validation errors

# Plan async support
/sf-plan-project Add async/await variants of all query and CRUD operations using aiohttp for non-blocking Salesforce API calls
```
