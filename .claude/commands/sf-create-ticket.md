---
name: sf-create-ticket
description: Create a comprehensive Jira ticket for sf-utils Salesforce library development, automatically generating detailed context, acceptance criteria, and technical specifications using six specialist agents: pragmatic-shipper, prd-writer, api-design-reviewer, qa-quality-assurance, security-auditor, and principal-code-reviewer
argument-hint: "<high-level description of work needed>"
---

Transform high-level user input into a well-structured Jira ticket with comprehensive details for sf-utils Python library development. This command uses a core team of six agents (`pragmatic-shipper`, `prd-writer`, `api-design-reviewer`, `qa-quality-assurance`, `security-auditor`, `principal-code-reviewer`) to handle all feature planning and specification in parallel.

**Target Platform**: Python 3.9+ library wrapping Salesforce REST APIs via SalesforcePy. Published to PyPI.

**Pragmatic Startup Philosophy**:

  - **Ship Fast**: Focus on working solutions over perfect implementations.
  - **80/20 Rule**: Deliver 80% of the value with 20% of the effort.
  - **MVP First**: Define the simplest thing that could possibly work.

**Smart Ticket Scoping**: Automatically breaks down large work into smaller, shippable tickets if the estimated effort exceeds 2 days.

**Important**: This command ONLY creates the ticket(s). It does not start implementation or modify any code.

## Core Agent Team

| Agent | Role in Ticket Creation |
|-------|------------------------|
| **prd-writer** | Requirements, acceptance criteria, user stories, success metrics |
| **pragmatic-shipper** | Codebase research, technical context, implementation plan, effort estimate |
| **api-design-reviewer** | Public API design, function signatures, parameter patterns, developer experience |
| **qa-quality-assurance** | Test strategy, key test cases, mocking approach, automation plan |
| **security-auditor** | Security implications, credential handling, SOQL injection, Salesforce-specific risks |
| **principal-code-reviewer** | Code quality standards, architectural patterns, Python best practices |

## Process

1. **Launch agents in parallel** (single message with multiple Task tool calls):
   - **prd-writer**: Create detailed requirements and acceptance criteria
   - **pragmatic-shipper**: Research codebase, identify technical dependencies, risks, and implementation considerations
   - **api-design-reviewer**: Define public API design — function signatures, parameter order, return types, error handling patterns, consistency with existing API
   - **qa-quality-assurance**: Define test strategy — unit tests with mocked SalesforcePy, edge cases, error scenarios
   - **security-auditor**: Identify security implications — credential handling, SOQL injection risks, logging safety
   - **principal-code-reviewer**: Identify code quality requirements — file structure, module organization, docstring standards

2. **Synthesize findings** from all agents into a comprehensive ticket

3. **Create the Jira ticket** using the jira-cli with all synthesized information

## Ticket Generation Process

### 1) Smart Research Depth Analysis

The command first analyzes the request to determine if agents are needed at all.

LIGHT Complexity → NO AGENTS
- For typos, simple config changes, minor tweaks.
- Create the ticket immediately.
- Estimate: <2 hours.

STANDARD / DEEP Complexity → CORE AGENTS
- For new features, bug fixes, new API modules, and architectural work.
- Agents are dispatched in parallel.
- The depth (Standard vs. Deep) determines the scope of their investigation.

**Override Flags (optional)**:

  - `--light`: Force minimal research (no agents).
  - `--standard` / `--deep`: Force investigation using agents.
  - `--single` / `--multi`: Control ticket splitting.
  - `--nosec`: Skip security-auditor (use sparingly).
  - `--noapi`: Skip api-design-reviewer (internal-only changes).

### 2) Scaled Investigation Strategy

#### LIGHT Research Pattern (Trivial Tickets)

NO AGENTS NEEDED.
1. Generate ticket title and description directly from the request.
2. Set pragmatic estimate (e.g., 1 hour).
3. Create ticket and finish.

#### STANDARD Research Pattern (Default for Features)

Agents are dispatched with a standard scope:

   - **prd-writer**: Define user stories, acceptance criteria, and success metrics.
   - **pragmatic-shipper**: Analyze implementation approach, identify risks, and estimate effort.
   - **api-design-reviewer**: Define public API changes — new functions, parameter design, consistency with existing patterns.
   - **qa-quality-assurance**: Define test strategy and key test cases.
   - **security-auditor**: Identify security considerations and requirements.
   - **principal-code-reviewer**: Identify code quality requirements.

#### DEEP Spike Pattern (Complex or Vague Tickets)

Agents are dispatched with a deeper scope:

   - **prd-writer**: Develop comprehensive user stories, business impact, and success metrics.
   - **pragmatic-shipper**: Analyze architectural trade-offs, identify key risks, and create a phased implementation roadmap.
   - **api-design-reviewer**: Full API design review — consistency audit across all modules, breaking change analysis, deprecation strategy, developer experience optimization.
   - **qa-quality-assurance**: Comprehensive test strategy — unit, integration, edge cases, error scenarios, SalesforcePy mocking patterns.
   - **security-auditor**: Thorough security analysis — credential flow audit, SOQL injection vectors, logging review, Salesforce API token handling.
   - **principal-code-reviewer**: Full code quality review — module organization, file size limits, docstring completeness, type hint coverage.

### 3) Generate Ticket Content

Findings from agents are synthesized into a comprehensive ticket.

#### Description Structure

```markdown
## Business Context & Purpose
<Synthesized from prd-writer findings>
- What problem are we solving and for whom?
- What is the expected impact for library users?

## Expected Behavior/Outcome
<Synthesized from prd-writer and api-design-reviewer findings>
- A clear description of the new functionality
- Public API additions/changes
- Example usage code

## Research Summary
**Investigation Depth**: <LIGHT|STANDARD|DEEP>
**Confidence Level**: <High|Medium|Low>

### Key Findings
- **Product & User Story**: <Key insights from prd-writer>
- **API Design**: <Key insights from api-design-reviewer>
- **Technical Plan & Risks**: <Key insights from pragmatic-shipper>
- **Security Assessment**: <Key insights from security-auditor>
- **Code Quality**: <Key insights from principal-code-reviewer>
- **Pragmatic Effort Estimate**: <From pragmatic-shipper>
- **Test Plan**: <From qa-quality-assurance>

## Acceptance Criteria
<Generated from all agents' findings>
- [ ] Functional Criterion: Function returns expected results for valid input
- [ ] API Criterion (from api-design-reviewer): Function signature follows existing patterns (positional required, keyword optional, client last)
- [ ] Error Criterion: Appropriate exceptions raised with clear messages
- [ ] Security Criterion (from security-auditor): No credentials logged, SOQL properly constructed
- [ ] Documentation Criterion: Docstring with Args, Returns, Raises, Example
- [ ] Test Criterion: Unit tests with mocked SalesforcePy client
- [ ] All new code paths are covered by tests

## Dependencies & Constraints
<Identified by pragmatic-shipper>
- **Dependencies**: SalesforcePy version requirements, new Python dependencies
- **Technical Constraints**: File size limits (600 lines max), backward compatibility

## Security Considerations
<Identified by security-auditor>
- **Risk Level**: <Low|Medium|High|Critical>
- **Key Security Requirements**: <Credential handling, SOQL construction, logging safety>
- **Salesforce-Specific Risks**: <API token exposure, rate limiting, sandbox vs production>

## API Design Notes
<Identified by api-design-reviewer>
- **New Public Functions**: List with signatures
- **Parameter Pattern**: Follows existing convention (required positional, optional keyword, client last)
- **Return Type**: Consistent with existing functions
- **Breaking Changes**: None / Listed with migration path

## Implementation Notes
<Technical guidance synthesized from all agents>
- **Recommended Approach**: Extend the existing module with...
- **Potential Gotchas**: SalesforcePy returns (body, status) tuples; handle consistently
```

### 4) Smart Ticket Creation

  - **If total estimated effort is <= 2 days**: A single, comprehensive ticket is created.
  - **If total estimated effort is > 2 days**: The work is automatically broken down into 2-3 smaller, interconnected tickets (e.g., "Part 1: Core functionality," "Part 2: Error handling," "Part 3: Documentation and tests"), each with its own scope and estimate.

### 5) Jira Ticket Creation

Write ticket body to temp file first, then create:

```bash
# Write body to temp file (avoids HEREDOC issues)
cat > /tmp/jira-ticket-body.md << 'EOF'
<ticket body content>
EOF

# Create ticket referencing the file
jira issue create --project DEV --type Task --summary "<title>" --body "$(cat /tmp/jira-ticket-body.md)"

# Clean up
rm /tmp/jira-ticket-body.md
```

### 6) Output & Confirmation

The command finishes by returning the URL(s) of the newly created ticket(s) in Jira.
