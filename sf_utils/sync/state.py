"""Sync state tracking for incremental Salesforce syncs.

Provides watermark tracking via the sf_sync_state PostgreSQL table.
Supports incremental syncs with advisory locks for concurrent protection.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from psycopg2 import extensions, sql

logger = logging.getLogger(__name__)


@dataclass
class SyncStateRow:
    """Represents a sync state row from sf_sync_state table.

    Attributes:
        object_name: Salesforce object name (e.g., 'Account', 'Contact').
        last_sync_timestamp: Timestamp of last successful sync.
        last_sync_id: Optional ID of last synced record (for pagination).
        sync_mode: Sync mode ('incremental' or 'full'). Default 'incremental'.
        updated_at: When this row was last updated.
    """

    object_name: str
    last_sync_timestamp: datetime
    last_sync_id: Optional[str] = None
    sync_mode: str = "incremental"
    updated_at: Optional[datetime] = None


def _compute_advisory_lock_key(object_name: str) -> int:
    """Compute deterministic advisory lock key for object name.

    Uses SHA-256 hash truncated to 63 bits (PostgreSQL bigint range).
    Ensures same object_name always gets same lock key.

    Args:
        object_name: Salesforce object name.

    Returns:
        Integer lock key in range [0, 2^63-1].

    Example:
        >>> _compute_advisory_lock_key("Account")
        4611686018427387903  # Deterministic, repeatable
        >>> _compute_advisory_lock_key("Contact")
        2305843009213693951  # Different object = different key
    """
    logger.debug("Computing advisory lock key for object: %s", object_name)

    # Hash object_name to bytes
    hash_bytes = hashlib.sha256(object_name.encode("utf-8")).digest()

    # Convert first 8 bytes to integer
    hash_int = int.from_bytes(hash_bytes[:8], byteorder="big")

    # Truncate to 63 bits (PostgreSQL bigint max: 2^63 - 1)
    lock_key = hash_int & 0x7FFFFFFFFFFFFFFF

    logger.debug("Advisory lock key for %s: %d", object_name, lock_key)
    return lock_key


def ensure_sync_state_table(db_conn: extensions.connection) -> None:
    """Create sf_sync_state table if it doesn't exist.

    Args:
        db_conn: Active psycopg2 connection.

    Raises:
        psycopg2.Error: On database errors.

    Example:
        >>> from sf_utils.db import get_connection
        >>> conn = get_connection()
        >>> ensure_sync_state_table(conn)
    """
    logger.debug("Ensuring sf_sync_state table exists")

    create_table_sql = sql.SQL("""
        CREATE TABLE IF NOT EXISTS sf_sync_state (
            object_name TEXT PRIMARY KEY,
            last_sync_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
            last_sync_id TEXT,
            sync_mode TEXT DEFAULT 'incremental',
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    cursor = db_conn.cursor()
    cursor.execute(create_table_sql)
    db_conn.commit()
    logger.debug("sf_sync_state table ensured")


def get_sync_state(
    object_name: str,
    db_conn: extensions.connection,
) -> Optional[SyncStateRow]:
    """Get sync state for a Salesforce object with advisory lock.

    Acquires pg_advisory_xact_lock() for concurrent sync protection.
    Lock is automatically released at transaction end.

    Args:
        object_name: Salesforce object name (e.g., 'Account').
        db_conn: Active psycopg2 connection.

    Returns:
        SyncStateRow if state exists, None if no previous sync.

    Raises:
        psycopg2.Error: On database errors.

    Example:
        >>> from sf_utils.db import get_connection
        >>> conn = get_connection()
        >>> state = get_sync_state("Account", conn)
        >>> if state:
        ...     print(f"Last sync: {state.last_sync_timestamp}")
        ... else:
        ...     print("No previous sync, use epoch")
    """
    logger.debug("Getting sync state for object: %s", object_name)

    # Compute advisory lock key
    lock_key = _compute_advisory_lock_key(object_name)

    cursor = db_conn.cursor()

    # Acquire advisory lock (transaction-scoped, released on commit/rollback)
    logger.debug("Acquiring advisory lock for %s (key=%d)", object_name, lock_key)
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
    cursor.fetchone()  # Consume the result

    # Query sync state
    select_query = sql.SQL(
        "SELECT object_name, last_sync_timestamp, last_sync_id, sync_mode, updated_at"
        " FROM sf_sync_state WHERE object_name = %s"
    )
    cursor.execute(select_query, (object_name,))

    row = cursor.fetchone()
    if row is None:
        logger.info("No sync state found for object: %s", object_name)
        return None

    # Unpack row
    obj_name, last_sync_ts, last_sync_id, sync_mode, updated_at = row

    state = SyncStateRow(
        object_name=obj_name,
        last_sync_timestamp=last_sync_ts,
        last_sync_id=last_sync_id,
        sync_mode=sync_mode,
        updated_at=updated_at,
    )

    logger.info(
        "Retrieved sync state for %s: last_sync=%s mode=%s",
        object_name,
        last_sync_ts,
        sync_mode,
    )
    return state


def update_sync_state(
    object_name: str,
    timestamp: datetime,
    db_conn: extensions.connection,
    sync_id: Optional[str] = None,
    mode: str = "incremental",
) -> None:
    """Update sync state for a Salesforce object with advisory lock.

    Uses UPSERT pattern (INSERT ... ON CONFLICT DO UPDATE).
    Acquires pg_advisory_xact_lock() for concurrent sync protection.

    Args:
        object_name: Salesforce object name (e.g., 'Account').
        timestamp: Timestamp of successful sync (must be timezone-aware).
        db_conn: Active psycopg2 connection.
        sync_id: Optional ID of last synced record.
        mode: Sync mode ('incremental' or 'full'). Default 'incremental'.

    Raises:
        ValueError: If timestamp is not timezone-aware.
        psycopg2.Error: On database errors.

    Example:
        >>> from datetime import datetime, timezone
        >>> from sf_utils.db import get_connection
        >>> conn = get_connection()
        >>> update_sync_state(
        ...     object_name="Account",
        ...     timestamp=datetime.now(timezone.utc),
        ...     db_conn=conn,
        ...     sync_id="001abc123",
        ...     mode="incremental"
        ... )
    """
    # Validate timezone-aware datetime
    if timestamp.tzinfo is None:
        raise ValueError(
            "timestamp must be timezone-aware (use datetime.now(timezone.utc))"
        )

    logger.debug(
        "Updating sync state for object: %s timestamp=%s sync_id=%s mode=%s",
        object_name,
        timestamp,
        sync_id,
        mode,
    )

    # Compute advisory lock key
    lock_key = _compute_advisory_lock_key(object_name)

    cursor = db_conn.cursor()

    # Acquire advisory lock (transaction-scoped, released on commit/rollback)
    logger.debug("Acquiring advisory lock for %s (key=%d)", object_name, lock_key)
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
    cursor.fetchone()  # Consume the result

    # Upsert sync state
    upsert_query = sql.SQL(
        "INSERT INTO sf_sync_state (object_name, last_sync_timestamp, last_sync_id, sync_mode, updated_at)"
        " VALUES (%s, %s, %s, %s, NOW())"
        " ON CONFLICT (object_name)"
        " DO UPDATE SET"
        " last_sync_timestamp = EXCLUDED.last_sync_timestamp,"
        " last_sync_id = EXCLUDED.last_sync_id,"
        " sync_mode = EXCLUDED.sync_mode,"
        " updated_at = NOW()"
    )
    cursor.execute(upsert_query, (object_name, timestamp, sync_id, mode))

    # Caller controls transaction, do NOT commit here

    logger.info(
        "Updated sync state for %s: timestamp=%s mode=%s",
        object_name,
        timestamp,
        mode,
    )
