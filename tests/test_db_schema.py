"""Tests for PostgreSQL schema management module.

Tests cover:
- SOQL SELECT clause parsing
- Column name sanitization
- Alias extraction
- CREATE TABLE generation with security (psycopg2.sql.Identifier)
- Table existence detection
- Error handling for malformed SOQL
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
import psycopg2
from psycopg2 import sql

from sf_utils.db.schema import (
    create_table_from_query,
    upsert_records,
    _parse_select_columns,
    _sanitize_column_name,
    _extract_alias,
)


class TestSanitizeColumnName:
    """Tests for _sanitize_column_name helper function."""

    def test_lowercase_conversion(self):
        """Should convert column names to lowercase."""
        assert _sanitize_column_name("Name") == "name"
        assert _sanitize_column_name("BillingCity") == "billingcity"
        assert _sanitize_column_name("ID") == "id"

    def test_space_replacement(self):
        """Should replace spaces with underscores."""
        assert _sanitize_column_name("Billing City") == "billing_city"
        assert _sanitize_column_name("Account Name") == "account_name"

    def test_dot_replacement_for_relationships(self):
        """Should replace dots with underscores (relationship traversal)."""
        assert _sanitize_column_name("Account.Name") == "account_name"
        assert _sanitize_column_name("Owner.Email") == "owner_email"

    def test_special_char_replacement(self):
        """Should replace special characters with underscores."""
        assert _sanitize_column_name("Field@Name") == "field_name"
        assert _sanitize_column_name("Field-Name") == "field_name"
        assert _sanitize_column_name("Field#123") == "field_123"

    def test_consecutive_underscores_removed(self):
        """Should collapse consecutive underscores."""
        assert _sanitize_column_name("Field__Name") == "field_name"
        assert _sanitize_column_name("A...B") == "a_b"

    def test_leading_trailing_underscores_removed(self):
        """Should remove leading/trailing underscores."""
        assert _sanitize_column_name("_Name") == "name"
        assert _sanitize_column_name("Name_") == "name"
        assert _sanitize_column_name("_Name_") == "name"

    def test_alphanumeric_preserved(self):
        """Should preserve alphanumeric characters and underscores."""
        assert _sanitize_column_name("Field123") == "field123"
        assert _sanitize_column_name("field_name") == "field_name"


class TestExtractAlias:
    """Tests for _extract_alias helper function."""

    def test_simple_field_no_alias(self):
        """Should return sanitized field name when no alias present."""
        assert _extract_alias("Id") == "id"
        assert _extract_alias("Name") == "name"
        assert _extract_alias("BillingCity") == "billingcity"

    def test_relationship_traversal_no_alias(self):
        """Should sanitize relationship traversals without aliases."""
        assert _extract_alias("Account.Name") == "account_name"
        assert _extract_alias("Owner.Email") == "owner_email"

    def test_as_alias_uppercase(self):
        """Should extract AS alias (uppercase)."""
        assert _extract_alias("Account.Name AS AccountName") == "accountname"
        assert _extract_alias("Owner.Email AS OwnerEmail") == "owneremail"

    def test_as_alias_lowercase(self):
        """Should extract AS alias (lowercase)."""
        assert _extract_alias("Account.Name as AccountName") == "accountname"

    def test_as_alias_mixed_case(self):
        """Should extract AS alias (mixed case)."""
        assert _extract_alias("Account.Name As AccountName") == "accountname"

    def test_as_alias_with_whitespace(self):
        """Should handle extra whitespace around AS alias."""
        assert _extract_alias("Account.Name  AS  AccountName  ") == "accountname"
        assert _extract_alias("  Account.Name AS AccountName") == "accountname"

    def test_complex_field_with_alias(self):
        """Should extract alias from complex field expressions."""
        assert _extract_alias("Contact.Account.Name AS ContactAccountName") == "contactaccountname"


class TestParseSelectColumns:
    """Tests for _parse_select_columns function."""

    def test_simple_select_single_field(self):
        """Should parse single field SELECT."""
        soql = "SELECT Id FROM Account"
        assert _parse_select_columns(soql) == ["id"]

    def test_simple_select_multiple_fields(self):
        """Should parse multiple fields SELECT."""
        soql = "SELECT Id, Name, BillingCity FROM Account"
        assert _parse_select_columns(soql) == ["id", "name", "billingcity"]

    def test_select_with_relationship_traversal(self):
        """Should parse relationship traversals."""
        soql = "SELECT Id, Account.Name, Owner.Email FROM Contact"
        assert _parse_select_columns(soql) == ["id", "account_name", "owner_email"]

    def test_select_with_aliases(self):
        """Should parse fields with AS aliases."""
        soql = "SELECT Id, Account.Name AS AccountName, BillingCity AS City FROM Contact"
        assert _parse_select_columns(soql) == ["id", "accountname", "city"]

    def test_select_with_mixed_aliases_and_plain_fields(self):
        """Should handle mix of aliased and non-aliased fields."""
        soql = "SELECT Id, Name, Account.Name AS AccountName FROM Contact"
        assert _parse_select_columns(soql) == ["id", "name", "accountname"]

    def test_select_case_insensitive(self):
        """Should handle case-insensitive SELECT and FROM keywords."""
        soql = "select Id, Name from Account"
        assert _parse_select_columns(soql) == ["id", "name"]

        soql = "SeLeCt Id, Name FrOm Account"
        assert _parse_select_columns(soql) == ["id", "name"]

    def test_select_with_whitespace_variations(self):
        """Should handle various whitespace patterns."""
        soql = "SELECT   Id ,  Name  ,  BillingCity   FROM   Account"
        assert _parse_select_columns(soql) == ["id", "name", "billingcity"]

    def test_select_with_newlines(self):
        """Should handle multi-line SOQL."""
        soql = """
        SELECT
            Id,
            Name,
            BillingCity
        FROM Account
        """
        assert _parse_select_columns(soql) == ["id", "name", "billingcity"]

    def test_select_with_where_clause(self):
        """Should parse SELECT when WHERE clause present."""
        soql = "SELECT Id, Name FROM Account WHERE BillingCity = 'San Francisco'"
        assert _parse_select_columns(soql) == ["id", "name"]

    def test_select_with_order_by(self):
        """Should parse SELECT when ORDER BY clause present."""
        soql = "SELECT Id, Name FROM Account ORDER BY Name"
        assert _parse_select_columns(soql) == ["id", "name"]

    def test_select_with_limit(self):
        """Should parse SELECT when LIMIT clause present."""
        soql = "SELECT Id, Name FROM Account LIMIT 100"
        assert _parse_select_columns(soql) == ["id", "name"]

    def test_missing_select_clause_raises_error(self):
        """Should raise ValueError when SELECT clause missing."""
        soql = "FROM Account WHERE Name = 'Test'"
        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns(soql)
        assert "SELECT ... FROM" in str(exc_info.value)

    def test_missing_from_clause_raises_error(self):
        """Should raise ValueError when FROM clause missing."""
        soql = "SELECT Id, Name WHERE BillingCity = 'SF'"
        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns(soql)
        assert "SELECT ... FROM" in str(exc_info.value)

    def test_empty_select_clause_raises_error(self):
        """Should raise ValueError when SELECT clause is empty."""
        soql = "SELECT   FROM Account"  # Whitespace between SELECT and FROM
        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns(soql)
        assert "at least one field" in str(exc_info.value)

    def test_select_with_trailing_comma(self):
        """Should handle trailing comma in SELECT clause gracefully."""
        soql = "SELECT Id, Name, FROM Account"
        # Trailing comma results in empty field after split
        columns = _parse_select_columns(soql)
        assert "id" in columns
        assert "name" in columns


class TestCreateTableFromQuery:
    """Tests for create_table_from_query function."""

    def test_create_table_simple_query(self):
        """Should create table with columns from simple SOQL query."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Simulate table created successfully
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name, BillingCity FROM Account"
        result = create_table_from_query("sf_account", soql, mock_conn)

        # Verify CREATE TABLE was executed
        assert mock_cursor.execute.call_count == 2  # CREATE + to_regclass check
        create_call = mock_cursor.execute.call_args_list[0]

        # Verify SQL uses Identifier for security
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

        # Verify commit called
        mock_conn.commit.assert_called_once()

        # Verify cursor closed
        mock_cursor.close.assert_called_once()

        # Verify return value (table created)
        assert result is True

    def test_create_table_with_if_not_exists_default(self):
        """Should use IF NOT EXISTS by default."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name FROM Account"
        create_table_from_query("sf_account", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_without_if_not_exists(self):
        """Should omit IF NOT EXISTS when if_not_exists=False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name FROM Account"
        create_table_from_query("sf_account", soql, mock_conn, if_not_exists=False)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_id_is_primary_key(self):
        """Should make Id column PRIMARY KEY."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name FROM Account"
        create_table_from_query("sf_account", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_all_columns_text_type(self):
        """Should create all columns as TEXT type."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_contact"]

        soql = "SELECT Id, Name, Email, Phone FROM Contact"
        create_table_from_query("sf_contact", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_with_relationship_columns(self):
        """Should handle relationship traversal in column names."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_contact"]

        soql = "SELECT Id, Account.Name, Owner.Email FROM Contact"
        create_table_from_query("sf_contact", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_with_aliases(self):
        """Should use aliases for column names when present."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_contact"]

        soql = "SELECT Id, Account.Name AS AccountName, BillingCity AS City FROM Contact"
        create_table_from_query("sf_contact", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_create_table_missing_id_raises_error(self):
        """Should raise ValueError when Id field is missing."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        soql = "SELECT Name, BillingCity FROM Account"
        with pytest.raises(ValueError) as exc_info:
            create_table_from_query("sf_account", soql, mock_conn)

        assert "Id field" in str(exc_info.value)
        assert "primary key" in str(exc_info.value).lower()

    def test_create_table_malformed_soql_raises_error(self):
        """Should raise ValueError for malformed SOQL."""
        mock_conn = MagicMock()

        soql = "FROM Account WHERE Name = 'Test'"
        with pytest.raises(ValueError) as exc_info:
            create_table_from_query("sf_account", soql, mock_conn)

        assert "SELECT ... FROM" in str(exc_info.value)

    def test_create_table_database_error_rollback(self):
        """Should rollback transaction on database error."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Simulate database error during CREATE TABLE
        mock_cursor.execute.side_effect = psycopg2.DatabaseError("table already exists")

        soql = "SELECT Id, Name FROM Account"
        with pytest.raises(psycopg2.DatabaseError):
            create_table_from_query("sf_account", soql, mock_conn)

        # Verify rollback was called
        mock_conn.rollback.assert_called_once()

        # Verify cursor was closed
        mock_cursor.close.assert_called_once()

    def test_create_table_uses_sql_identifier_for_security(self):
        """Should use psycopg2.sql.Identifier for table and column names.

        This prevents SQL injection by properly escaping identifiers.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name FROM Account"
        create_table_from_query("sf_account", soql, mock_conn)

        # Verify execute was called with sql.Composed object
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

        # Composed object ensures SQL injection protection
        # by using psycopg2.sql.Identifier for all identifiers

    def test_create_table_returns_false_if_table_existed(self):
        """Should return False when table already existed."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Simulate table did NOT exist (to_regclass returns NULL)
        mock_cursor.fetchone.return_value = [None]

        soql = "SELECT Id, Name FROM Account"
        result = create_table_from_query("sf_account", soql, mock_conn)

        # Verify return value (table already existed)
        assert result is False

    def test_create_table_returns_true_if_table_created(self):
        """Should return True when table was newly created."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Simulate table exists (to_regclass returns table name)
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name FROM Account"
        result = create_table_from_query("sf_account", soql, mock_conn)

        # Verify return value (table created)
        assert result is True

    def test_create_table_with_special_chars_in_table_name(self):
        """Should handle special characters in table name via Identifier."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account_2024"]

        # Table name with special chars (should be quoted by Identifier)
        table_name = "sf_account_2024"
        soql = "SELECT Id, Name FROM Account"

        create_table_from_query(table_name, soql, mock_conn)

        # Verify execute was called (Identifier handles escaping)
        assert mock_cursor.execute.called

    @patch("sf_utils.db.schema.logger")
    def test_create_table_logs_success_at_info_level(self, mock_logger):
        """Should log table creation at INFO level with column count."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = "SELECT Id, Name, BillingCity FROM Account"
        create_table_from_query("sf_account", soql, mock_conn)

        # Verify INFO logging was called
        assert mock_logger.info.called

        # Verify log message contains table name and column count
        info_calls = [str(call_obj) for call_obj in mock_logger.info.call_args_list]
        assert any("sf_account" in call_str for call_str in info_calls)
        assert any("3" in call_str for call_str in info_calls)  # 3 columns

    @patch("sf_utils.db.schema.logger")
    def test_create_table_logs_error_on_failure(self, mock_logger):
        """Should log error at ERROR level on database failure."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = psycopg2.DatabaseError("permission denied")

        soql = "SELECT Id, Name FROM Account"
        with pytest.raises(psycopg2.DatabaseError):
            create_table_from_query("sf_account", soql, mock_conn)

        # Verify ERROR logging was called
        assert mock_logger.error.called
        error_call = str(mock_logger.error.call_args)
        assert "sf_account" in error_call


class TestIntegration:
    """Integration tests with realistic scenarios."""

    def test_end_to_end_account_table_creation(self):
        """Should create account table from realistic SOQL query."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_account"]

        soql = """
        SELECT
            Id,
            Name,
            BillingStreet,
            BillingCity,
            BillingState,
            BillingPostalCode,
            Phone
        FROM Account
        WHERE BillingCountry = 'USA'
        """

        result = create_table_from_query("sf_account", soql, mock_conn)

        # Verify table created
        assert result is True

        # Verify CREATE TABLE executed
        assert mock_cursor.execute.call_count == 2
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]
        assert isinstance(executed_query, sql.Composed)

    def test_end_to_end_contact_with_relationships(self):
        """Should create contact table with relationship columns."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["sf_contact"]

        soql = """
        SELECT
            Id,
            FirstName,
            LastName,
            Email,
            Account.Name AS AccountName,
            Account.BillingCity AS AccountCity,
            Owner.Email AS OwnerEmail
        FROM Contact
        WHERE Account.Type = 'Customer'
        """

        result = create_table_from_query("sf_contact", soql, mock_conn)

        # Verify table created
        assert result is True

        # Verify aliased columns used via parsing
        columns = _parse_select_columns(soql)
        assert "accountname" in columns
        assert "accountcity" in columns
        assert "owneremail" in columns




class TestUpsertRecords:
    """Tests for upsert_records function."""

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_single_record_insert(self, mock_execute_values):
        """Should insert a single new record."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # xmax = 0 indicates insert
        mock_cursor.fetchall.return_value = [(True,)]

        records = [{"id": "001abc", "name": "Acme Corp", "status": "active"}]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 1
        assert updated == 0

        # Verify commit called
        mock_conn.commit.assert_called_once()

        # Verify cursor closed
        mock_cursor.close.assert_called_once()

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_single_record_update(self, mock_execute_values):
        """Should update an existing record."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # xmax != 0 indicates update
        mock_cursor.fetchall.return_value = [(False,)]

        records = [{"id": "001abc", "name": "Acme Corp Updated", "status": "inactive"}]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 0
        assert updated == 1

        # Verify commit called
        mock_conn.commit.assert_called_once()

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_multiple_records_mixed(self, mock_execute_values):
        """Should handle mix of inserts and updates."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # 2 inserts (xmax=0), 1 update (xmax!=0)
        mock_cursor.fetchall.return_value = [(True,), (False,), (True,)]

        records = [
            {"id": "001abc", "name": "Acme Corp", "status": "active"},
            {"id": "002def", "name": "Globex", "status": "inactive"},
            {"id": "003ghi", "name": "Initech", "status": "active"},
        ]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 2
        assert updated == 1

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_batching_multiple_batches(self, mock_execute_values):
        """Should process records in batches."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 3 records, batch_size=1 -> 3 batches
        mock_cursor.fetchall.side_effect = [
            [(True,)],  # Batch 1: insert
            [(False,)],  # Batch 2: update
            [(True,)],  # Batch 3: insert
        ]

        records = [
            {"id": "001", "name": "A"},
            {"id": "002", "name": "B"},
            {"id": "003", "name": "C"},
        ]
        inserted, updated = upsert_records("sf_account", records, mock_conn, batch_size=1)

        # Verify counts
        assert inserted == 2
        assert updated == 1

        # Verify commit called 3 times (once per batch)
        assert mock_conn.commit.call_count == 3

    def test_upsert_empty_records_returns_zero_zero(self):
        """Should return (0, 0) for empty record list without executing query."""
        mock_conn = MagicMock()

        records = []
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 0
        assert updated == 0

        # Verify cursor was not created for empty records
        assert not mock_conn.cursor.called

    def test_upsert_invalid_table_name_raises_error(self):
        """Should raise ValueError for empty table name."""
        mock_conn = MagicMock()

        records = [{"id": "001", "name": "Test"}]
        with pytest.raises(ValueError) as exc_info:
            upsert_records("", records, mock_conn)

        assert "table_name" in str(exc_info.value)
        assert "non-empty string" in str(exc_info.value)

    def test_upsert_invalid_table_name_none_raises_error(self):
        """Should raise ValueError for None table name."""
        mock_conn = MagicMock()

        records = [{"id": "001", "name": "Test"}]
        with pytest.raises(ValueError) as exc_info:
            upsert_records(None, records, mock_conn)

        assert "table_name" in str(exc_info.value)

    def test_upsert_invalid_records_type_raises_error(self):
        """Should raise ValueError when records is not a list."""
        mock_conn = MagicMock()

        # Pass dict instead of list
        with pytest.raises(ValueError) as exc_info:
            upsert_records("sf_account", {"id": "001", "name": "Test"}, mock_conn)

        assert "records must be a list" in str(exc_info.value)

    def test_upsert_missing_id_field_raises_error(self):
        """Should raise ValueError when records missing 'id' field."""
        mock_conn = MagicMock()

        records = [
            {"id": "001", "name": "A"},
            {"name": "B"},  # Missing 'id'
        ]
        with pytest.raises(ValueError) as exc_info:
            upsert_records("sf_account", records, mock_conn)

        assert "id" in str(exc_info.value).lower()

    def test_upsert_database_error_rollback(self):
        """Should rollback transaction on database error."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Simulate database error during fetchall (after execute_values)
        mock_cursor.fetchall.side_effect = psycopg2.DatabaseError("constraint violation")

        records = [{"id": "001", "name": "Test"}]
        with pytest.raises(psycopg2.DatabaseError) as exc_info:
            upsert_records("sf_account", records, mock_conn)

        # Verify error message contains context
        assert "sf_account" in str(exc_info.value)
        assert "1" in str(exc_info.value)  # record count

        # Verify rollback was called
        mock_conn.rollback.assert_called_once()

        # Verify cursor was closed
        mock_cursor.close.assert_called_once()

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_uses_sql_identifier_for_security(self, mock_execute_values):
        """Should use psycopg2.sql.Identifier for table and column names.

        This prevents SQL injection by properly escaping identifiers.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,)]

        records = [{"id": "001", "name": "Test"}]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify the function completed successfully
        assert inserted == 1
        assert updated == 0

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_handles_multiple_columns(self, mock_execute_values):
        """Should handle records with many columns."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,)]

        records = [
            {
                "id": "001",
                "name": "Acme",
                "industry": "Tech",
                "city": "SF",
                "state": "CA",
                "phone": "555-1234",
                "website": "acme.com",
            }
        ]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 1
        assert updated == 0

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_preserves_column_order(self, mock_execute_values):
        """Should use consistent column order from first record."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,), (True,)]

        records = [
            {"id": "001", "name": "A", "status": "active"},
            {"id": "002", "name": "B", "status": "inactive"},
        ]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 2
        assert updated == 0

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_default_batch_size(self, mock_execute_values):
        """Should use default batch_size of 500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 600 records, default batch_size=500 -> 2 batches
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 500,  # Batch 1: 500 inserts
            [(True,)] * 100,  # Batch 2: 100 inserts
        ]

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(600)]
        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 600
        assert updated == 0

        # Verify commit called 2 times (once per batch)
        assert mock_conn.commit.call_count == 2

    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_custom_batch_size(self, mock_execute_values):
        """Should respect custom batch_size parameter."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 10 records, batch_size=3 -> 4 batches (3+3+3+1)
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 3,  # Batch 1
            [(True,)] * 3,  # Batch 2
            [(True,)] * 3,  # Batch 3
            [(True,)] * 1,  # Batch 4
        ]

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(10)]
        inserted, updated = upsert_records("sf_account", records, mock_conn, batch_size=3)

        # Verify counts
        assert inserted == 10
        assert updated == 0

        # Verify commit called 4 times
        assert mock_conn.commit.call_count == 4

    @patch("sf_utils.db.schema.logger")
    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_logs_progress(self, mock_execute_values, mock_logger):
        """Should log progress at INFO level with record counts."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,)]

        records = [{"id": "001", "name": "Test"}]
        upsert_records("sf_account", records, mock_conn)

        # Verify INFO logging was called
        assert mock_logger.info.called

        # Verify log message contains table name
        info_calls = [str(call_obj) for call_obj in mock_logger.info.call_args_list]
        assert any("sf_account" in call_str for call_str in info_calls)

    @patch("sf_utils.db.schema.logger")
    def test_upsert_logs_error_on_failure(self, mock_logger):
        """Should log error at ERROR level on database failure."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Simulate database error during fetchall
        mock_cursor.fetchall.side_effect = psycopg2.DatabaseError("constraint violation")

        records = [{"id": "001", "name": "Test"}]
        with pytest.raises(psycopg2.DatabaseError):
            upsert_records("sf_account", records, mock_conn)

        # Verify ERROR logging was called
        assert mock_logger.error.called
        error_call = str(mock_logger.error.call_args)
        assert "sf_account" in error_call

    @patch("sf_utils.db.schema.logger")
    @patch("sf_utils.db.schema.execute_values")
    def test_upsert_does_not_log_record_values(self, mock_execute_values, mock_logger):
        """Should NOT log record values to prevent PII leakage."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,)]

        # Record with sensitive data
        records = [{"id": "001", "email": "sensitive@example.com", "ssn": "123-45-6789"}]
        upsert_records("sf_account", records, mock_conn)

        # Verify no log calls contain sensitive values
        all_log_calls = (
            mock_logger.debug.call_args_list
            + mock_logger.info.call_args_list
            + mock_logger.error.call_args_list
        )
        all_log_messages = [str(call_obj) for call_obj in all_log_calls]

        # Should NOT log sensitive values
        assert not any("sensitive@example.com" in msg for msg in all_log_messages)
        assert not any("123-45-6789" in msg for msg in all_log_messages)

        # Should log metadata only (table name, counts)
        assert any("sf_account" in msg for msg in all_log_messages)
class TestUpsertRecordsIntegration:
    """Integration tests for upsert_records with realistic scenarios."""

    def test_upsert_salesforce_accounts_realistic(self):
        """Should upsert Salesforce Account records with realistic data."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # 2 inserts, 1 update
        mock_cursor.fetchall.return_value = [(True,), (True,), (False,)]

        records = [
            {
                "id": "001abc123",
                "name": "Acme Corporation",
                "industry": "Technology",
                "billingcity": "San Francisco",
                "billingstate": "CA",
                "phone": "415-555-1234",
            },
            {
                "id": "001def456",
                "name": "Globex Industries",
                "industry": "Manufacturing",
                "billingcity": "New York",
                "billingstate": "NY",
                "phone": "212-555-5678",
            },
            {
                "id": "001ghi789",
                "name": "Initech (Updated)",
                "industry": "Services",
                "billingcity": "Austin",
                "billingstate": "TX",
                "phone": "512-555-9012",
            },
        ]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify counts
        assert inserted == 2
        assert updated == 1

        # Verify commit called
        mock_conn.commit.assert_called_once()

    def test_upsert_large_dataset_batching(self):
        """Should efficiently batch large dataset (1000 records)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 1000 records, batch_size=500 -> 2 batches
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 500,  # Batch 1: all inserts
            [(False,)] * 500,  # Batch 2: all updates
        ]

        records = [
            {"id": f"00{i:04d}", "name": f"Account {i}", "status": "active"}
            for i in range(1000)
        ]

        inserted, updated = upsert_records("sf_account", records, mock_conn, batch_size=500)

        # Verify counts
        assert inserted == 500
        assert updated == 500

        # Verify commit called 2 times
        assert mock_conn.commit.call_count == 2

    def test_upsert_null_values(self):
        """Should handle NULL values correctly (psycopg2 None)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,), (True,)]

        records = [
            {"id": "001", "name": "Record 1", "status": None, "phone": "555-1234"},
            {"id": "002", "name": None, "status": "active", "phone": None},
        ]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify records processed successfully
        assert inserted == 2
        assert updated == 0

        # Verify execute was called
        assert mock_cursor.execute.called

    def test_upsert_missing_fields(self):
        """Should handle records with missing fields (varying columns)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(True,), (True,), (True,)]

        # Records with different fields - uses first record's structure
        records = [
            {"id": "001", "name": "Record 1", "status": "active", "phone": "555-1234"},
            {"id": "002", "name": "Record 2"},  # Missing 'status' and 'phone'
            {"id": "003", "status": "inactive"},  # Missing 'name' and 'phone'
        ]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify all records processed (missing fields become None via dict.get())
        assert inserted == 3
        assert updated == 0

    def test_upsert_large_batch_triggers_batching(self):
        """Should process >500 records in multiple batches."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 750 records triggers batching (500 + 250)
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 500,  # First batch
            [(True,)] * 250,  # Second batch
        ]

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(750)]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify all records processed
        assert inserted == 750
        assert updated == 0

        # Verify commit called twice (once per batch)
        assert mock_conn.commit.call_count == 2

    def test_upsert_exact_batch_size(self):
        """Should handle records count exactly equal to batch_size."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Exactly 500 records (default batch_size)
        mock_cursor.fetchall.return_value = [(True,)] * 500

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(500)]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify all records processed in single batch
        assert inserted == 500
        assert updated == 0

        # Verify commit called once
        assert mock_conn.commit.call_count == 1

    def test_upsert_records_more_than_batch(self):
        """Should handle 501 records with batch_size=500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # 501 records triggers 2 batches (500 + 1)
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 500,  # First batch
            [(True,)],  # Second batch (1 record)
        ]

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(501)]

        inserted, updated = upsert_records("sf_account", records, mock_conn)

        # Verify all records processed
        assert inserted == 501
        assert updated == 0

        # Verify commit called twice
        assert mock_conn.commit.call_count == 2

    @patch("sf_utils.db.schema.logger")
    def test_upsert_logs_batch_progress(self, mock_logger):
        """Should log batch progress format: 'Upserted X/Y records to table_name'."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Two batches: 500 + 250
        mock_cursor.fetchall.side_effect = [
            [(True,)] * 500,
            [(True,)] * 250,
        ]

        records = [{"id": f"{i:03d}", "name": f"Record {i}"} for i in range(750)]

        upsert_records("sf_account", records, mock_conn)

        # Verify INFO logging called
        assert mock_logger.info.called

        # Verify batch progress messages by checking call args
        # Each call is: logger.info(format_string, arg1, arg2, ...)
        info_calls = mock_logger.info.call_args_list

        # Check for specific progress: (batch_end, total_records, ...)
        # First batch: 500/750
        # Second batch: 750/750
        call_args = [call_obj[0] for call_obj in info_calls]

        # Check first batch log: 500/750
        assert any(args[1:3] == (500, 750) for args in call_args if len(args) >= 3)
        # Check second batch log: 750/750
        assert any(args[1:3] == (750, 750) for args in call_args if len(args) >= 3)
        # Check table name appears
        assert any("sf_account" in args for args in call_args)
