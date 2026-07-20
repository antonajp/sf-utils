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
```

**Activate virtual environment:**

| Platform | Command |
|----------|---------|
| Linux/macOS | `source .venv/bin/activate` |
| Windows PowerShell | `.\.venv\Scripts\Activate.ps1` |
| Windows CMD | `.venv\Scripts\activate.bat` |

```bash
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

## Data Sync

The sync system enables you to synchronize Salesforce data to a local PostgreSQL database using either REST API or Bulk API 2.0. Syncs are configured declaratively using YAML files.

### Quick Start

Get started in 5 steps:

**1. Set up environment variables** (see Configuration section above)

**2. Create a SOQL template** at `soql/account.soql`:

```sql
SELECT Id, Name, Industry, LastModifiedDate
FROM Account
WHERE LastModifiedDate >= {start_date}
  AND LastModifiedDate < {end_date}
```

**3. Create a sync configuration** at `sync_config.yaml`:

```yaml
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    chunk_size: daily
    mode: auto
    enabled: true
```

**4. Install the CLI:**

```bash
pip install -e .
```

**5. Run your first sync:**

```bash
sf-sync sync Account
```

### CLI Commands

The `sf-sync` command provides three main operations:

```bash
# Sync a single object
sf-sync sync Account

# Sync all enabled objects from config
sf-sync sync --all

# Preview sync without executing
sf-sync sync --dry-run Account

# Force specific API mode
sf-sync sync --mode bulk Account    # Use Bulk API 2.0
sf-sync sync --mode rest Account    # Use REST API
sf-sync sync --mode auto Account    # Auto-detect (default)

# Use custom config file
sf-sync sync --config ./my_config.yaml Account

# Enable debug logging
sf-sync sync --verbose Account

# Check sync status
sf-sync status                      # Table format
sf-sync status --json               # JSON format
```

### SOQL Templates

SOQL templates support placeholders for incremental syncs:

| Placeholder | Description |
|-------------|-------------|
| `{start_date}` | Start of date range (ISO 8601) |
| `{end_date}` | End of date range (ISO 8601) |
| `{watermark}` | Last sync timestamp |

**Example templates:**

Account template (`soql/account.soql`):
```sql
SELECT Id, Name, Industry, Type, CreatedDate, LastModifiedDate
FROM Account
WHERE LastModifiedDate >= {start_date}
  AND LastModifiedDate < {end_date}
```

Contact template (`soql/contact.soql`):
```sql
SELECT Id, FirstName, LastName, Email, AccountId, LastModifiedDate
FROM Contact
WHERE LastModifiedDate >= {start_date}
  AND LastModifiedDate < {end_date}
```

Opportunity template (`soql/opportunity.soql`):
```sql
SELECT Id, Name, StageName, Amount, CloseDate, AccountId, LastModifiedDate
FROM Opportunity
WHERE LastModifiedDate >= {start_date}
  AND LastModifiedDate < {end_date}
```

### Sync Configuration

Define sync jobs in `sync_config.yaml`:

```yaml
syncs:
  # Required fields
  - object_name: Account              # Salesforce object name
    soql_file: soql/account.soql      # Path to SOQL template
    date_field: LastModifiedDate      # Date field for incremental sync

    # Optional fields (defaults shown)
    chunk_size: daily                 # hourly, daily, weekly, monthly, none
    mode: auto                        # auto, rest, bulk
    enabled: true                     # Include in --all sync

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: LastModifiedDate
    chunk_size: weekly
    mode: bulk                        # Force Bulk API
    enabled: true

  - object_name: Opportunity
    soql_file: soql/opportunity.soql
    date_field: LastModifiedDate
    enabled: false                    # Excluded from --all
```

**Configuration Options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `object_name` | Yes | - | Salesforce object name (e.g., Account, Contact) |
| `soql_file` | Yes | - | Path to SOQL template file |
| `date_field` | Yes | - | Date/datetime field for incremental sync tracking |
| `chunk_size` | No | daily | Time interval for chunking queries |
| `mode` | No | auto | API mode: auto, rest, or bulk |
| `enabled` | No | true | Whether to include in `--all` sync |

### REST vs Bulk API Mode Selection

| Scenario | Recommended Mode | Reason |
|----------|------------------|--------|
| < 10,000 records | REST (`rest`) | Lower latency, simpler |
| > 10,000 records | Bulk (`bulk`) | Avoids API limits, handles large datasets |
| Unknown size | Auto (`auto`) | Queries count first, then chooses |

**Auto mode behavior:**
1. Runs `SELECT COUNT(Id) FROM {object}` to get record count
2. If count < 10,000, uses REST API
3. If count >= 10,000, uses Bulk API 2.0

### Programmatic Usage

```python
from sf_utils.sync import sync, SyncMode
from sf_utils.sync.config import load_sync_config

# Load sync jobs from config
configs = load_sync_config("sync_config.yaml")

for config in configs:
    if config.enabled:
        result = sync(
            soql="SELECT Id, Name FROM " + config.object_name,
            object_name=config.object_name,
            mode=SyncMode.AUTO,
            date_field=config.date_field,
        )
        print(f"Synced {result.records_fetched} records from {config.object_name}")
```

### Cross-Platform Support

The CLI works consistently on Linux, macOS, and Windows:

| Platform | Terminal | Example |
|----------|----------|---------|
| Linux | bash/zsh | `sf-sync sync Account` |
| macOS | bash/zsh/Terminal | `sf-sync sync Account` |
| Windows | PowerShell | `sf-sync sync Account` |
| Windows | CMD | `sf-sync sync Account` |

**Windows notes:**
- Use PowerShell for best experience
- Path separators in YAML files can be `/` or `\` (forward slash preferred)
- Environment variables work identically

### Troubleshooting

**Common Issues:**

| Error | Cause | Solution |
|-------|-------|----------|
| `Missing Salesforce credentials` | SF_* env vars not set | Check `.env` file and source it |
| `Failed to connect to PostgreSQL` | Database not running | Run `docker start sf-utils-postgres` |
| `Config file not found` | YAML path incorrect | Check `sync_config.yaml` exists |
| `Object not found in config` | Object name not in YAML | Add object to `syncs:` list |
| `SOQL file not found` | Bad path in config | Verify `soql_file` path |

**Debug logging:**

```bash
# Enable verbose output
sf-sync sync --verbose Account

# Check sync status
sf-sync status
```

**Reset sync state:**

```sql
-- Connect to PostgreSQL
psql -h localhost -U postgres -d sf_utils

-- View sync state
SELECT * FROM sf_sync_state;

-- Reset a specific object (force full sync next time)
DELETE FROM sf_sync_state WHERE object_name = 'Account';
```

## Development

### Run Tests

Linux/macOS:
```bash
source .venv/bin/activate
pytest tests/ -v
```

Windows PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
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
