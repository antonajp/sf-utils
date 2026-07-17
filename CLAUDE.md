# Salesforce Utilities

Python utility functions for Salesforce operations using the [SalesforcePy](https://github.com/forcedotcom/SalesforcePy) library.

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

### Rules
- **Modular Design**: No Python file exceeds 600 lines.
- **Environment Safety**: Never hardcode secrets. Use `.env` files (gitignored) or environment variables.
- **Test-First**: Write tests before implementation.
- **Clean Architecture**: Separate concerns (client, query, sobjects).
- **Documentation**: Update docstrings and keep code self-documenting.

### Tech Stack
- **Language**: Python 3.9+
- **Salesforce**: SalesforcePy 2.2+
- **Config**: python-dotenv
- **Testing**: pytest, pytest-cov
- **CI/CD**: GitHub Actions (planned)

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
SF_USERNAME=user@example.com
SF_PASSWORD=password
SF_CLIENT_ID=connected-app-client-id
SF_CLIENT_SECRET=connected-app-secret
SF_SANDBOX=false          # true for sandbox orgs
SF_API_VERSION=v61.0      # optional
```

### Key Design Decisions
- All functions accept optional `client` parameter; creates one from env if not provided
- SalesforcePy returns `(body, status)` tuples; utilities handle this consistently
- Sandbox vs production determined by `SF_SANDBOX` env var (uses test.salesforce.com)
- Pagination handled automatically in `query_all()`
