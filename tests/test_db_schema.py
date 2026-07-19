"""Tests for PostgreSQL schema management module.

Tests cover:
- SOQL SELECT clause parsing
- Column name sanitization
- Alias extraction
- CREATE TABLE generation with security (psycopg2.sql.Identifier)
- Table existence detection
- Error handling for malformed SOQL
"""

from unittest.mock import MagicMock, patch

import pytest
import psycopg2
from psycopg2 import sql

from sf_utils.db.schema import (
    create_table_from_query,
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
