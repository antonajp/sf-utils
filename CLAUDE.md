# Salesforce Utilities

Python utility functions for Salesforce operations using the [simple-salesforce](https://github.com/simple-salesforce/simple-salesforce) library with JWT Bearer OAuth support.

## Jira Project
All tickets for this project use the **DEV** project in Jira.

## Git Repository
https://github.com/antonajp/sf-utils

### Workflow
1. Create a feature branch from `main`.
2. Develop and test locally.
3. Open a PR targeting `main`.
4. Repo owner can push directly; others require PR approval.

## Agent Team

| Agent | Role | When to Use |
|-------|------|-------------|
| **pragmatic-shipper** | Feature implementation | Starting any development work |
| **principal-code-reviewer** | Code quality review | After implementing features, before PRs |
| **api-design-reviewer** | API design & DX | Adding/changing public functions |
| **security-auditor** | Security vulnerabilities | Touching auth, credentials, or SOQL |
| **qa-quality-assurance** | Test automation | Writing tests, validating coverage |
| **prd-writer** | Requirements docs | Planning new API modules |
| **jira-integration-engineer** | Jira ticket mgmt | Creating/updating tickets |

### Review Flow
Every code change passes 4 review lenses:
1. **Code quality** (principal-code-reviewer)
2. **API design** (api-design-reviewer)
3. **Security** (security-auditor)
4. **Testing** (qa-quality-assurance)

## Orchestration Commands

Commands are managed in the `base-claude-flow` repo and loaded into `.claude/commands/`.

### `/sf-create-ticket <description>`
Create a Jira ticket with parallel agent analysis. Agents research codebase, define requirements, API design, security considerations, and test strategy.

```bash
/sf-create-ticket Add support for Salesforce Bulk API 2.0
/sf-create-ticket --light Fix typo in docstring    # Skip agent research
/sf-create-ticket --deep Complex architectural change
```

**Flags**: `--light` (no agents), `--standard`/`--deep` (research depth), `--nosec`, `--noapi`

### `/sf-develop-ticket <ticket-id>`
Implement a single Jira ticket with iterative review cycles. Creates branch, implements with parallel agents, runs code/API/security reviews, iterates until approved, commits.

```bash
/sf-develop-ticket DEV-123
/sf-develop-ticket DEV-123 --nosec    # Skip security review
```

**Phases**: Git setup → Retrieve ticket → Parallel implementation → Review/audit → Iterate → Test → Commit

### `/sf-plan-project <description>`
Generate a comprehensive user story WBS with sprint planning. Interactive Q&A gathers context, then agents produce epics, stories, estimates, and dependencies.

```bash
/sf-plan-project Add async support for all query operations
```

**Output**: `docs/sf-utils-user-stories.md`

### `/sf-sprint-runner <tickets|--cycle status>`
Batch execute multiple tickets end-to-end: implement, push, PR, CI, merge, repeat. Handles dependencies, sends email reports.

```bash
/sf-sprint-runner DEV-123 DEV-124 DEV-125
/sf-sprint-runner --cycle "In Progress"    # All tickets in status
/sf-sprint-runner --dry-run DEV-123        # Preview only
/sf-sprint-runner --resume                 # Continue after failure
```

**Flags**: `--dry-run`, `--resume [path]`, `--cycle <status>`

### Command Hierarchy
```
sf-plan-project          # Planning: generates user stories → docs/
    ↓
sf-create-ticket         # Atomic: creates single Jira ticket
    ↓
sf-develop-ticket        # Atomic: implements single ticket (used by sprint-runner)
    ↓
sf-sprint-runner         # Batch: orchestrates multiple tickets with CI/merge
```

### Rules
- **Modular Design**: No Python file exceeds 600 lines.
- **Environment Safety**: Never hardcode secrets. Use `.env` files (gitignored) or environment variables.
- **Test-First**: Write tests before implementation.
- **Clean Architecture**: Separate concerns (client, query, sobjects).
- **Documentation**: Update docstrings and keep code self-documenting.

### Tech Stack
- **Language**: Python 3.9+
- **Salesforce**: simple-salesforce 1.12+ (JWT Bearer OAuth & password flow)
- **JWT Auth**: PyJWT 2.8+, cryptography 41.0+
- **Database**: PostgreSQL (Docker container, psycopg2)
- **Export**: openpyxl (Excel), csv (stdlib)
- **Config**: python-dotenv
- **CLI**: click (cross-platform argument parsing)
- **Testing**: pytest, pytest-cov
- **CI/CD**: GitHub Actions (planned)

### Cross-Platform Standards

This library must work on Linux, macOS, and Windows (PowerShell):

- **Paths**: Always use `pathlib.Path`, never string concatenation with `/` or `\`
- **CLI**: Use `click` with `path_type=Path` for file arguments
- **Environment**: Use `python-dotenv` for .env files (works identically everywhere)
- **Temp Files**: Use `tempfile` module for cross-platform temporary directories
- **Subprocess**: Avoid shell-specific syntax; use list form of subprocess calls
- **Testing**: GitHub Actions matrix must include `ubuntu-latest`, `macos-latest`, `windows-latest`

```python
# Correct - cross-platform
from pathlib import Path
config_path = Path("soql") / "accounts.soql"

# Incorrect - breaks on Windows
config_path = "soql/" + "accounts.soql"
```

### Project Structure
```
sf_utils/
├── __init__.py      # Public API exports
├── client.py        # get_client(), SalesforceConfig
├── query.py         # query(), query_all() - SOQL with pagination
└── sobjects.py      # CRUD: get/create/update/upsert/delete/describe

tests/
└── test_client.py   # Unit tests with mocked SF client
```

### Environment Variables
```
# Salesforce (Password Flow)
SF_USERNAME=user@example.com
SF_PASSWORD=password
SF_CLIENT_ID=connected-app-client-id
SF_CLIENT_SECRET=connected-app-secret
SF_SANDBOX=false          # true for sandbox orgs
SF_API_VERSION=v61.0      # optional

# Salesforce (JWT Bearer Flow - for MFA-enabled orgs)
SF_USERNAME=user@example.com
SF_CLIENT_ID=connected-app-client-id
SF_PRIVATE_KEY_PATH=/path/to/server.key
SF_PRIVATE_KEY_PASSPHRASE=optional-passphrase  # if key is encrypted
SF_SANDBOX=false

# PostgreSQL (Docker container)
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=sf_utils
PG_USER=postgres
PG_PASSWORD=your-password
```

## Data Flow Pattern

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Salesforce    │      │   PostgreSQL    │      │  Excel / CSV    │
│   (REST API)    │ ──→  │   (Docker)      │ ──→  │   (Export)      │
└─────────────────┘      └─────────────────┘      └─────────────────┘
     query()              sync/cache               analyze/export
     query_all()          local storage            aggregate reports
```

**Phase 1: Sync** - Query Salesforce data and cache locally in PostgreSQL
- Implementations create tables as needed (no strict schema)
- Raw psycopg2 for database access
- Each clone has its own Docker PostgreSQL instance

**Phase 2: Analyze** - Query local PostgreSQL and produce reports
- Aggregate data, compute metrics
- Export to Excel (.xlsx) or CSV
- No live Salesforce connection needed

### Key Design Decisions
- All functions accept optional `client` parameter; creates one from env if not provided
- Auto-detects JWT vs password auth based on `SF_PRIVATE_KEY_PATH` environment variable
- simple-salesforce raises exceptions on errors; utilities wrap these in typed exceptions
- Sandbox vs production determined by `SF_SANDBOX` env var (uses test.salesforce.com)
- Pagination handled automatically in `query_all()`
- PostgreSQL is loosely coupled - each clone has its own Docker container
- Database schema is flexible - implementations create tables as needed
- Use parameterized queries only (prevent SQL injection)
