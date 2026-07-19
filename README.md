# sf-utils

Python utility functions for Salesforce operations with local PostgreSQL caching and Excel/CSV export.

## Features

- **Salesforce REST API** - Query, CRUD operations via [SalesforcePy](https://github.com/forcedotcom/SalesforcePy)
- **Local Caching** - Sync Salesforce data to PostgreSQL for offline analysis
- **Export** - Generate Excel (.xlsx) and CSV reports from cached data

## Data Privacy

**All data stays on your local machine.**

| Component | Location | Cloud Exposure |
|-----------|----------|----------------|
| PostgreSQL | Docker container on developer's machine | None |
| Excel/CSV exports | Local filesystem | None |
| Credentials | `.env` file (gitignored) | None |

This architecture is designed for organizations where Salesforce data must remain within the corporate firewall:

- **PostgreSQL** runs in a local Docker instance on the developer's computer — no cloud database services involved
- **Excel and CSV files** are generated locally and only leave the machine through deliberate user action (email, Slack, file sharing, etc.)
- **No data is transmitted to external cloud services** unless you explicitly choose to do so

The only external connection is to Salesforce itself (to query/sync data), which is already within your organization's Salesforce tenant

## Installation

```bash
# Clone the repository
git clone https://github.com/antonajp/sf-utils.git
cd sf-utils

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

### Salesforce Credentials

```env
SF_USERNAME=your-username@example.com
SF_PASSWORD=your-password
SF_CLIENT_ID=your-connected-app-client-id
SF_CLIENT_SECRET=your-connected-app-client-secret
SF_SANDBOX=false          # true for sandbox orgs
SF_API_VERSION=v61.0      # optional
```

**Setup a Connected App in Salesforce:**
1. Setup → Apps → App Manager → New Connected App
2. Enable OAuth Settings
3. Add scopes: `api`, `refresh_token`
4. Copy Consumer Key (Client ID) and Consumer Secret

### PostgreSQL (Docker)

```env
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=sf_utils
PG_USER=postgres
PG_PASSWORD=postgres
```

**Setup PostgreSQL (recommended):**

Linux / macOS:
```bash
./scripts/setup-db.sh
```

Windows PowerShell:
```powershell
.\scripts\setup-db.ps1
```

These scripts handle all edge cases automatically:
- Stop and remove existing `sf-utils-postgres` containers
- Pull the latest PostgreSQL 16 image
- Create a new container bound to localhost only
- Wait for the database to be ready

**Or start manually:**

```bash
docker run -d \
  --name sf-utils-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=sf_utils \
  -p 127.0.0.1:5432:5432 \
  postgres:16
```

## Usage

### Salesforce Client

```python
from sf_utils import get_client, SalesforceConfig

# Auto-load from environment
client = get_client()

# Or explicit configuration
config = SalesforceConfig(
    username="user@example.com",
    password="password",
    client_id="xxx",
    client_secret="xxx",
    sandbox=True
)
client = get_client(config=config)
```

### SOQL Queries

```python
from sf_utils import query, query_all

# Single batch (up to 2000 records)
accounts = query("SELECT Id, Name FROM Account WHERE Industry = 'Technology'")

# All records with automatic pagination
all_contacts = query_all("SELECT Id, FirstName, LastName, Email FROM Contact")
```

### CRUD Operations

```python
from sf_utils import get_record, create_record, update_record, delete_record

# Read
account = get_record("Account", "001XXXXXXXXXXXX")

# Create
new_id = create_record("Account", {
    "Name": "Acme Corp",
    "Industry": "Technology"
})

# Update
update_record("Account", new_id, {"Industry": "Software"})

# Delete
delete_record("Account", new_id)
```

### Upsert by External ID

```python
from sf_utils import upsert_record

result = upsert_record(
    "Account",
    external_id_field="External_Id__c",
    external_id_value="EXT-001",
    data={"Name": "Updated Corp", "Industry": "Consulting"}
)

print(f"ID: {result['id']}, Created: {result['created']}")
```

### Describe Object Metadata

```python
from sf_utils import describe_object

metadata = describe_object("Account")
fields = [f["name"] for f in metadata["fields"]]
```

## Data Flow Pattern

```
                        ┌──────────────────────────────────────────────┐
                        │           YOUR LOCAL MACHINE                 │
┌─────────────────┐     │  ┌─────────────────┐      ┌───────────────┐  │
│   Salesforce    │     │  │   PostgreSQL    │      │  Excel / CSV  │  │
│   (Your Org)    │ ──────→│   (Docker)      │ ──→  │   (Local)     │  │
└─────────────────┘     │  └─────────────────┘      └───────────────┘  │
                        │                                              │
                        │  No data leaves this boundary automatically  │
                        └──────────────────────────────────────────────┘
```

1. **Sync**: Query Salesforce and cache data locally in PostgreSQL
2. **Analyze**: Query local database, compute aggregates
3. **Export**: Generate Excel or CSV reports locally
4. **Distribute** (optional, user-initiated): Share via corporate email, Slack, etc.

## Development

### Run Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

### Project Structure

```
sf_utils/
├── __init__.py      # Public API exports
├── client.py        # get_client(), SalesforceConfig
├── query.py         # query(), query_all()
└── sobjects.py      # CRUD operations

tests/
└── test_client.py   # Unit tests with mocked SF client
```

## License

MIT
