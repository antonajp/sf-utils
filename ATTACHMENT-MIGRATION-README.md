# Salesforce Attachment Migration Tool

Migrate Salesforce Attachments to ContentDocuments (Files) from **any sObject** using tiered batch processing.

## Overview

Salesforce is deprecating Classic Attachments in favor of Salesforce Files (ContentDocument/ContentVersion). This CLI tool automates the migration by:

- **Supporting any sObject type** - Migrate attachments from Account, Contact, Case, or any custom object
- Processing attachments hour-by-hour to manage governor limits
- Segmenting by file size (BodyLength) with appropriate batch sizes
- Executing existing Apex batch classes via Anonymous Apex
- Supporting cursor-based recovery on failure
- **Concurrent migrations** - Each sObject uses its own cursor file

## Prerequisites

### Salesforce Org Requirements

1. **Connected App** configured for JWT Bearer or Password OAuth flow
2. **Apex Batch Classes** deployed to your org:
   - `AttachmentToFilesConversionBatch` - Standard batch processing
   - `SingleAttachmentToFilesConversionBatch` - Single-record processing for large files
3. **Custom Field** (for idempotent migrations):
   - `ContentVersion.Original_Record_Id__c` - Text(18), External ID, Unique

### Python Requirements

- Python 3.9+
- pip package manager

## Installation

```bash
# Clone the repository
git clone https://github.com/antonajp/sf-utils.git
cd sf-utils

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e .
```

## Environment Variables

Configure Salesforce authentication using environment variables. Create a `.env` file:

### JWT Bearer Flow (Recommended for MFA-enabled orgs)

```bash
SF_USERNAME=user@example.com
SF_CLIENT_ID=connected-app-consumer-key
SF_PRIVATE_KEY_PATH=/path/to/server.key
SF_PRIVATE_KEY_PASSPHRASE=optional-passphrase  # If key is encrypted
SF_SANDBOX=false                               # true for sandbox orgs
SF_API_VERSION=v61.0                           # Optional, defaults to v61.0
```

### Password Flow

```bash
SF_USERNAME=user@example.com
SF_PASSWORD=your-password
SF_CLIENT_ID=connected-app-consumer-key
SF_CLIENT_SECRET=connected-app-consumer-secret
SF_SANDBOX=false
SF_API_VERSION=v61.0
```

### Extending Batch Class Allowlist

Add custom batch classes via environment variable:

```bash
SF_CUSTOM_BATCH_CLASSES=MyCustomBatch,AnotherBatchClass
```

## Configuration

Create a `migration.properties` file to define batch processing tiers:

```properties
# Poll interval for checking batch job status (seconds)
poll_interval_seconds=10

# Tier 1: Small attachments (< 1 MB)
tier1.min_size=0
tier1.max_size=1048576
tier1.batch_size=10
tier1.batch_class=AttachmentToFilesConversionBatch

# Tier 2: Medium attachments (1-3 MB)
tier2.min_size=1048576
tier2.max_size=3145728
tier2.batch_size=3
tier2.batch_class=AttachmentToFilesConversionBatch

# Tier 3: Large attachments (3-6 MB)
tier3.min_size=3145728
tier3.max_size=6291456
tier3.batch_size=1
tier3.batch_class=AttachmentToFilesConversionBatch

# Tier 4: Extra-large attachments (>= 6 MB)
# Use max_size=-1 for no upper bound
tier4.min_size=6291456
tier4.max_size=-1
tier4.batch_size=1
tier4.batch_class=SingleAttachmentToFilesConversionBatch
```

### Tier Configuration

| Tier | Size Range | Batch Size | Batch Class |
|------|------------|------------|-------------|
| 1 | < 1 MB | 10 | AttachmentToFilesConversionBatch |
| 2 | 1-3 MB | 3 | AttachmentToFilesConversionBatch |
| 3 | 3-6 MB | 1 | AttachmentToFilesConversionBatch |
| 4 | >= 6 MB | 1 | SingleAttachmentToFilesConversionBatch |

Adjust batch sizes based on your org's heap limits. Smaller batch sizes are safer but slower.

## Usage

### Basic Migration

```bash
# Migrate Progress_Note__c attachments (custom object)
sf-sync migrate-attachments Progress_Note__c

# Migrate Account attachments
sf-sync migrate-attachments Account --start-date 2024-01-15

# Migrate Case attachments with custom config
sf-sync migrate-attachments Case --config ./case-migration.properties
```

### CLI Options

| Argument/Option | Description | Default |
|-----------------|-------------|---------|
| `SOBJECT_TYPE` | **Required.** Salesforce object API name (e.g., Account, Progress_Note__c) | - |
| `--start-date` | Start date in ISO format (YYYY-MM-DD or YYYY-MM-DDTHH) | 2023-06-01 |
| `--config` | Path to properties configuration file | migration.properties |
| `--resume` | Resume from last saved cursor position | Off |
| `--verbose` | Enable debug logging | Off |

### Examples

```bash
# Migrate Progress_Note__c from default start date
sf-sync migrate-attachments Progress_Note__c

# Migrate Account attachments from specific date
sf-sync migrate-attachments Account --start-date 2024-03-01

# Start from a specific hour (useful for recovery)
sf-sync migrate-attachments Contact --start-date 2024-03-10T14

# Resume from cursor after failure
sf-sync migrate-attachments Progress_Note__c --resume

# Verbose output for debugging
sf-sync migrate-attachments Case --verbose

# Combine options
sf-sync migrate-attachments Account --start-date 2024-01-01 --config ./prod-config.properties --verbose
```

## Idempotency

**IMPORTANT**: This tool does NOT prevent duplicate migrations at the Python layer. Idempotency depends entirely on your Apex batch class implementation.

### If your Apex batch class checks `Original_Record_Id__c`

Re-running the migration is safe; duplicates will be skipped.

### If your Apex batch class does NOT check for duplicates

**Risk**: Re-running the migration will create duplicate ContentVersion records for the same Attachments. This results in storage bloat and user confusion.

**Mitigation**:
- Only run the migration once per date range
- Manually delete duplicates after accidental re-runs
- Modify your Apex batch classes to implement idempotency checking

### Required Custom Field (for idempotent migrations)

Create on ContentVersion:
- **Field Name**: `Original_Record_Id__c`
- **Type**: Text(18)
- **External ID**: Yes (recommended for query performance)
- **Unique**: Yes (recommended)

Your Apex batch class should:
1. Query existing ContentVersions by `Original_Record_Id__c`
2. Skip Attachments that have already been converted

## Error Recovery

The tool automatically saves progress to a sObject-specific cursor file after each hour completes. For example:
- `Progress_Note__c` → `.progress_note__c_migration_cursor.json`
- `Account` → `.account_migration_cursor.json`

If the migration fails:

1. **Review the error message** - The tool displays what went wrong
2. **Resume from cursor** - Run with `--resume` to continue from where it stopped:

```bash
sf-sync migrate-attachments Progress_Note__c --resume
```

3. **Manual recovery** - Start from a specific hour if needed:

```bash
sf-sync migrate-attachments Account --start-date 2024-03-10T14
```

### Cursor File Format

The cursor file (e.g., `.progress_note__c_migration_cursor.json`) contains:

```json
{
  "last_completed_hour": "2024-03-10T13:00:00Z",
  "next_hour": "2024-03-10T14:00:00Z",
  "total_hours_processed": 150,
  "total_batches_executed": 600
}
```

## How It Works

1. **Hour-by-Hour Processing**: Iterates from start date to current time, one hour at a time
2. **Tiered Batch Execution**: For each hour, executes 4 batch jobs (one per tier) based on attachment size
3. **Anonymous Apex**: Uses Salesforce Tooling API to execute batch jobs via Anonymous Apex
4. **Job Polling**: Polls AsyncApexJob every 10 seconds until batch completes
5. **Cursor Persistence**: Saves progress after each hour for recovery

### Generated Apex (Per Tier)

```apex
String query = 'SELECT Id, Name, Body, ParentId, Description, OwnerId,
    CreatedDate, CreatedById, LastModifiedById, LastModifiedDate
    FROM Attachment
    WHERE BodyLength >= {min} AND BodyLength < {max}
    AND Parent.Id IN (SELECT Id FROM {SOBJECT_TYPE}
        WHERE CreatedDate >= {hour_start} AND CreatedDate < {hour_end})';
Id batchId = Database.executeBatch(new AttachmentToFilesConversionBatch(query, '{SOBJECT_LABEL}'), {batch_size});
```

## Platform Considerations

### Person Accounts

When migrating attachments from `Account` in a PersonAccount-enabled org, the query will return attachments from both person accounts and business accounts. Consider filtering by `Account.RecordType.DeveloperName` in your batch class if needed.

### Knowledge Articles

`KnowledgeArticle` and `KnowledgeArticleVersion` have special lifecycle management. Migrations may require handling article versions separately.

### Polymorphic ParentId

The `Attachment.ParentId` field is polymorphic - it can reference any sObject. The subquery pattern correctly filters by parent object type.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success - Migration completed |
| 1 | Failure - Check error message and use `--resume` to continue |

## Troubleshooting

### "Invalid sObject name"

The sObject name must:
- Start with a letter
- Contain only alphanumeric characters and underscores
- Be at most 40 characters

This validation prevents SOQL injection attacks.

### "Config file not found"

Ensure `migration.properties` exists in the current directory or specify the path:

```bash
sf-sync migrate-attachments Progress_Note__c --config /path/to/migration.properties
```

### "Authentication failed"

Verify environment variables are set correctly:

```bash
# Check if variables are set
echo $SF_USERNAME
echo $SF_CLIENT_ID
```

### "Batch job had errors"

Check Salesforce Setup > Apex Jobs for the failed batch details. Common causes:
- Heap size exceeded (reduce batch_size in config)
- Record locks (retry later)
- Validation rule failures
