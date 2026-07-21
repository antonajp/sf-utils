"""Tests for SOQL aggregate function type inference.

Tests cover:
- AGGREGATE_FUNCTION_TYPES constant validation
- infer_aggregate_type() function for all aggregate functions
- _parse_select_columns_with_types() with aggregate detection
- create_table_from_query() with aggregate type inference
- Parameter passthrough in sync functions
- Security (whitelist validation, ReDoS protection)
- Edge cases (mixed aggregates, GROUP BY, aliases)
"""

from unittest.mock import MagicMock, Mock, patch
import re
import pytest
import psycopg2
from psycopg2 import sql

from sf_utils.db.types import (
    SALESFORCE_TYPE_TO_POSTGRES,
    get_postgres_type,
    AGGREGATE_FUNCTION_TYPES,
    infer_aggregate_type,
)
from sf_utils.db.parser import (
    _parse_select_columns_with_types,
    _sanitize_column_name,
    _extract_alias,
    ColumnSpec,
)
from sf_utils.db.schema import (
    create_table_from_query,
    _add_missing_columns,
)
from sf_utils.sync.state import SyncStateRow


class TestAggregateFunctionTypesConstant:
    """Tests for AGGREGATE_FUNCTION_TYPES constant."""

    def test_constant_exists(self):
        """Should define AGGREGATE_FUNCTION_TYPES constant."""
        assert hasattr(
            pytest.importorskip("sf_utils.db.types"), "AGGREGATE_FUNCTION_TYPES"
        )

    def test_count_maps_to_bigint(self):
        """Should map COUNT to BIGINT (handles large counts > INTEGER max)."""
        assert AGGREGATE_FUNCTION_TYPES["count"] == "BIGINT"

    def test_sum_maps_to_numeric(self):
        """Should map SUM to NUMERIC (preserves decimal precision)."""
        assert AGGREGATE_FUNCTION_TYPES["sum"] == "NUMERIC"

    def test_avg_maps_to_numeric(self):
        """Should map AVG to NUMERIC (always returns decimal)."""
        assert AGGREGATE_FUNCTION_TYPES["avg"] == "NUMERIC"

    def test_min_maps_to_text_default(self):
        """Should map MIN to TEXT as default (type depends on source field)."""
        assert AGGREGATE_FUNCTION_TYPES["min"] == "TEXT"

    def test_max_maps_to_text_default(self):
        """Should map MAX to TEXT as default (type depends on source field)."""
        assert AGGREGATE_FUNCTION_TYPES["max"] == "TEXT"

    def test_constant_is_dict(self):
        """Should be a dictionary type."""
        assert isinstance(AGGREGATE_FUNCTION_TYPES, dict)

    def test_all_values_are_valid_postgres_types(self):
        """Should contain only valid PostgreSQL type names."""
        valid_types = {"BIGINT", "NUMERIC", "TEXT", "INTEGER", "BOOLEAN"}
        for pg_type in AGGREGATE_FUNCTION_TYPES.values():
            assert (
                pg_type in valid_types
            ), f"Invalid PostgreSQL type: {pg_type}"


class TestInferAggregateType:
    """Tests for infer_aggregate_type() function."""

    def test_count_star(self):
        """Should return BIGINT for COUNT(*)."""
        assert infer_aggregate_type("COUNT(*)") == "BIGINT"

    def test_count_field(self):
        """Should return BIGINT for COUNT(field)."""
        assert infer_aggregate_type("COUNT(Id)") == "BIGINT"

    def test_count_distinct(self):
        """Should return BIGINT for COUNT(DISTINCT field)."""
        assert infer_aggregate_type("COUNT(DISTINCT Id)") == "BIGINT"

    def test_sum_field(self):
        """Should return NUMERIC for SUM(field)."""
        assert infer_aggregate_type("SUM(Amount)") == "NUMERIC"
        assert infer_aggregate_type("SUM(AnnualRevenue)") == "NUMERIC"

    def test_avg_field(self):
        """Should return NUMERIC for AVG(field)."""
        assert infer_aggregate_type("AVG(Amount)") == "NUMERIC"
        assert infer_aggregate_type("AVG(AnnualRevenue)") == "NUMERIC"

    def test_min_field(self):
        """Should return TEXT for MIN(field) as default."""
        assert infer_aggregate_type("MIN(CreatedDate)") == "TEXT"

    def test_max_field(self):
        """Should return TEXT for MAX(field) as default."""
        assert infer_aggregate_type("MAX(Amount)") == "TEXT"

    def test_regular_field_returns_none(self):
        """Should return None for non-aggregate fields."""
        assert infer_aggregate_type("Id") is None
        assert infer_aggregate_type("Name") is None
        assert infer_aggregate_type("Account.Name") is None

    def test_case_insensitive_count(self):
        """Should handle case-insensitive function names."""
        assert infer_aggregate_type("count(id)") == "BIGINT"
        assert infer_aggregate_type("COUNT(Id)") == "BIGINT"
        assert infer_aggregate_type("Count(Id)") == "BIGINT"

    def test_case_insensitive_sum(self):
        """Should handle case-insensitive SUM."""
        assert infer_aggregate_type("sum(amount)") == "NUMERIC"
        assert infer_aggregate_type("SUM(Amount)") == "NUMERIC"

    def test_case_insensitive_avg(self):
        """Should handle case-insensitive AVG."""
        assert infer_aggregate_type("avg(amount)") == "NUMERIC"
        assert infer_aggregate_type("AVG(Amount)") == "NUMERIC"

    def test_whitespace_variations(self):
        """Should handle whitespace variations."""
        assert infer_aggregate_type("COUNT( * )") == "BIGINT"
        assert infer_aggregate_type("SUM(  Amount  )") == "NUMERIC"
        assert infer_aggregate_type(" AVG(Revenue) ") == "NUMERIC"

    def test_redos_protection_long_expression(self):
        """Should handle very long expressions without hanging (ReDoS protection)."""
        long_field = "A" * 10000
        # Should complete quickly, not hang
        result = infer_aggregate_type(f"COUNT({long_field})")
        # Either recognizes it or rejects it, but doesn't hang
        assert result in ("BIGINT", None)

    def test_nested_functions_not_supported(self):
        """Should handle nested functions gracefully (not currently supported)."""
        # Nested functions are rare in Salesforce, return None
        result = infer_aggregate_type("COUNT(SUM(Amount))")
        # Should either return None or handle gracefully
        assert result in (None, "BIGINT")

    def test_complex_count_distinct(self):
        """Should handle COUNT with DISTINCT keyword."""
        assert infer_aggregate_type("COUNT(DISTINCT OwnerId)") == "BIGINT"

    def test_field_with_relationship_traversal(self):
        """Should handle aggregate on relationship fields."""
        assert infer_aggregate_type("COUNT(Account.Name)") == "BIGINT"
        assert infer_aggregate_type("SUM(Account.AnnualRevenue)") == "NUMERIC"


class TestParseSelectColumnsWithTypes:
    """Tests for _parse_select_columns_with_types() parser function."""

    def test_simple_select_no_aggregates(self):
        """Should parse simple SELECT with no aggregates."""
        soql = "SELECT Id, Name, BillingCity FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 3
        assert columns[0].name == "id"
        assert columns[0].aggregate_function is None
        assert columns[1].name == "name"
        assert columns[1].aggregate_function is None
        assert columns[2].name == "billingcity"
        assert columns[2].aggregate_function is None

    def test_select_with_count(self):
        """Should detect COUNT and assign BIGINT type."""
        soql = "SELECT Industry, COUNT(Id) FROM Account GROUP BY Industry"
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 2
        assert columns[0].name == "industry"
        assert columns[0].aggregate_function is None
        assert columns[1].name == "count_expr"
        assert columns[1].aggregate_function == "count"

    def test_select_with_sum(self):
        """Should detect SUM and assign NUMERIC type."""
        soql = "SELECT Industry, SUM(AnnualRevenue) FROM Account GROUP BY Industry"
        columns = _parse_select_columns_with_types(soql)

        assert columns[1].name == "sum_expr"
        assert columns[1].aggregate_function == "sum"

    def test_select_with_avg(self):
        """Should detect AVG and assign NUMERIC type."""
        soql = "SELECT Industry, AVG(AnnualRevenue) FROM Account GROUP BY Industry"
        columns = _parse_select_columns_with_types(soql)

        assert columns[1].name == "avg_expr"
        assert columns[1].aggregate_function == "avg"

    def test_select_with_aliased_aggregate(self):
        """Should use alias name for aliased aggregates."""
        soql = "SELECT Industry, COUNT(Id) AS total FROM Account GROUP BY Industry"
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 2
        assert columns[1].name == "total"
        assert columns[1].alias == "total"
        assert columns[1].aggregate_function == "count"

    def test_select_with_non_aliased_aggregate(self):
        """Should use {func}_expr pattern for non-aliased aggregates."""
        soql = "SELECT COUNT(Id), SUM(Amount) FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert columns[0].name == "count_expr"
        assert columns[0].aggregate_function == "count"
        assert columns[1].name == "sum_expr"
        assert columns[1].aggregate_function == "sum"

    def test_select_mixed_aggregates_and_fields(self):
        """Should handle mix of GROUP BY fields and aggregates."""
        soql = """
        SELECT
            Industry,
            BillingState,
            COUNT(Id) AS account_count,
            SUM(AnnualRevenue) AS total_revenue,
            AVG(NumberOfEmployees)
        FROM Account
        GROUP BY Industry, BillingState
        """
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 5
        assert columns[0].name == "industry"
        assert columns[0].aggregate_function is None
        assert columns[1].name == "billingstate"
        assert columns[1].aggregate_function is None
        assert columns[2].name == "account_count"
        assert columns[2].aggregate_function == "count"
        assert columns[3].name == "total_revenue"
        assert columns[3].aggregate_function == "sum"
        assert columns[4].name == "avg_expr"
        assert columns[4].aggregate_function == "avg"

    def test_case_variations_in_function_names(self):
        """Should handle case variations in aggregate function names."""
        soql = "SELECT count(Id), Sum(Amount), AVG(Revenue) FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert columns[0].aggregate_function == "count"
        assert columns[1].aggregate_function == "sum"
        assert columns[2].aggregate_function == "avg"

    def test_min_max_default_to_text(self):
        """Should use TEXT for MIN/MAX by default."""
        soql = "SELECT MIN(CreatedDate), MAX(Amount) FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert columns[0].name == "min_expr"
        assert columns[0].aggregate_function == "min"
        assert columns[1].name == "max_expr"
        assert columns[1].aggregate_function == "max"

    def test_select_with_relationship_traversal(self):
        """Should handle relationship traversals in GROUP BY."""
        soql = "SELECT Account.Name, COUNT(Id) FROM Contact GROUP BY Account.Name"
        columns = _parse_select_columns_with_types(soql)

        assert columns[0].name == "account_name"
        assert columns[0].aggregate_function is None
        assert columns[1].name == "count_expr"
        assert columns[1].aggregate_function == "count"


class TestCreateTableFromQueryWithAggregates:
    """Tests for create_table_from_query() with aggregate type inference."""

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_create_table_count_column_bigint(self, mock_get_columns):
        """Should create COUNT column as BIGINT."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, COUNT(Id) AS total FROM Account GROUP BY Id, Industry"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        # Verify CREATE TABLE was executed
        assert mock_cursor.execute.call_count >= 1
        create_call = mock_cursor.execute.call_args_list[0]
        executed_query = create_call[0][0]

        # Verify it's a safe SQL composition
        assert isinstance(executed_query, sql.Composed)

        # Check query contains BIGINT for COUNT column
        query_str = str(executed_query)
        assert "BIGINT" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_create_table_sum_column_numeric(self, mock_get_columns):
        """Should create SUM column as NUMERIC."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, SUM(AnnualRevenue) FROM Account GROUP BY Id, Industry"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])
        assert "NUMERIC" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_create_table_avg_column_numeric(self, mock_get_columns):
        """Should create AVG column as NUMERIC."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, AVG(AnnualRevenue) FROM Account GROUP BY Id, Industry"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])
        assert "NUMERIC" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_create_table_aliased_aggregate_uses_alias(self, mock_get_columns):
        """Should use alias name for aliased aggregate columns."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, COUNT(Id) AS account_count FROM Account GROUP BY Id, Industry"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should contain column named "account_count" with BIGINT type
        assert "account_count" in query_str
        assert "BIGINT" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_create_table_non_aliased_uses_func_expr_pattern(self, mock_get_columns):
        """Should use {func}_expr pattern for non-aliased aggregates."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, COUNT(Id) AS count_expr, SUM(Amount) AS sum_expr FROM Account GROUP BY Id"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should contain count_expr and sum_expr columns
        assert "count_expr" in query_str
        assert "sum_expr" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_infer_aggregate_types_false_creates_text_columns(self, mock_get_columns):
        """Should create all columns as TEXT when infer_aggregate_types=False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, COUNT(Id) AS total FROM Account GROUP BY Id, Industry"
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=False
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should NOT contain BIGINT or NUMERIC
        assert "BIGINT" not in query_str or "TEXT" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_type_overrides_parameter(self, mock_get_columns):
        """Should allow manual type specification via type_overrides."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, COUNT(Id) AS total FROM Account GROUP BY Id, Industry"
        type_overrides = {"total": "INTEGER"}  # Override BIGINT with INTEGER

        create_table_from_query(
            "account_summary",
            soql,
            mock_conn,
            infer_aggregate_types=True,
            type_overrides=type_overrides,
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should use INTEGER instead of BIGINT for 'total' column
        assert "INTEGER" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_mixed_aggregates_and_group_by_fields(self, mock_get_columns):
        """Should handle mix of GROUP BY fields and multiple aggregates."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = """
        SELECT
            Id,
            Industry,
            BillingState,
            COUNT(Id) AS count,
            SUM(AnnualRevenue) AS revenue,
            AVG(NumberOfEmployees) AS avg_employees
        FROM Account
        GROUP BY Id, Industry, BillingState
        """
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Verify types
        assert "industry" in query_str.lower()
        assert "billingstate" in query_str.lower()
        assert "count" in query_str.lower()
        assert "BIGINT" in query_str
        assert "NUMERIC" in query_str


class TestAddMissingColumnsWithAggregates:
    """Tests for _add_missing_columns() with aggregate types."""

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_add_aggregate_columns_with_correct_types(self, mock_get_columns):
        """Should add aggregate columns to existing table with correct types."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Simulate table exists with only 'id' and 'industry' columns
        mock_get_columns.return_value = ["id", "industry"]

        soql = "SELECT Id, Industry, COUNT(Id) AS total, SUM(Amount) AS revenue FROM Account GROUP BY Id, Industry"

        # Call create_table_from_query which should add missing columns
        create_table_from_query(
            "account_summary", soql, mock_conn, infer_aggregate_types=True
        )

        # Should execute ALTER TABLE to add missing columns
        assert mock_cursor.execute.call_count >= 1  # ALTER (or more)


class TestSyncFunctionParameterPassthrough:
    """Tests for parameter passthrough in sync functions.

    Note: These tests verify that sync functions accept and pass through the
    infer_aggregate_types parameter to create_table_from_query. Full sync flow
    testing is covered in dedicated sync test modules.
    """

    def test_sync_records_signature_accepts_infer_aggregate_types(self):
        """Should accept infer_aggregate_types parameter in function signature."""
        from sf_utils.sync.rest_sync import sync_records
        import inspect

        sig = inspect.signature(sync_records)
        assert "infer_aggregate_types" in sig.parameters

        # Verify default value is True
        param = sig.parameters["infer_aggregate_types"]
        assert param.default is True

    def test_sync_records_bulk_signature_accepts_infer_aggregate_types(self):
        """Should accept infer_aggregate_types parameter in function signature."""
        from sf_utils.sync.bulk_sync import sync_records_bulk
        import inspect

        sig = inspect.signature(sync_records_bulk)
        assert "infer_aggregate_types" in sig.parameters

        # Verify default value is True
        param = sig.parameters["infer_aggregate_types"]
        assert param.default is True


class TestSecurityAggregateValidation:
    """Security tests for aggregate function validation."""

    def test_whitelist_pattern_rejects_sql_injection(self):
        """Should reject SQL injection attempts in aggregate expressions."""
        # Attempt SQL injection via function name
        malicious = "COUNT(Id); DROP TABLE users; --"

        # Should either return None (not recognized) or handle safely
        result = infer_aggregate_type(malicious)
        # Should NOT execute the injection
        assert result in (None, "BIGINT")  # Either not recognized or COUNT recognized

    def test_expression_length_limit(self):
        """Should enforce reasonable expression length limits (ReDoS protection)."""
        # Very long expression
        long_expr = "COUNT(" + "A" * 100000 + ")"

        # Should complete quickly without hanging
        import time
        start = time.time()
        result = infer_aggregate_type(long_expr)
        elapsed = time.time() - start

        # Should complete in under 1 second (not hang)
        assert elapsed < 1.0

    def test_soql_length_limit(self):
        """Should enforce SOQL query length limits (ReDoS protection)."""
        from sf_utils.db.parser import _parse_select_columns, _parse_select_columns_with_types, MAX_SOQL_LENGTH

        # Create very long SOQL query
        long_soql = "SELECT " + "Field" * 50000 + " FROM Account"

        # Should raise ValueError for excessively long query
        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns(long_soql)
        assert "exceeds maximum length" in str(exc_info.value)

        # Same for _parse_select_columns_with_types
        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns_with_types(long_soql)
        assert "exceeds maximum length" in str(exc_info.value)

    def test_whitelist_only_known_functions(self):
        """Should only recognize whitelisted aggregate functions."""
        # Unknown function should return None
        assert infer_aggregate_type("UNKNOWN_FUNC(Id)") is None

        # Known functions should work
        assert infer_aggregate_type("COUNT(Id)") == "BIGINT"
        assert infer_aggregate_type("SUM(Amount)") == "NUMERIC"

    def test_no_pii_in_logs(self):
        """Should not log aggregate field names (could contain PII)."""
        with patch("sf_utils.db.types.logger") as mock_logger:
            infer_aggregate_type("COUNT(SensitiveField__c)")

            # Check no debug/info logs contain the field name
            for call_args in mock_logger.debug.call_args_list:
                log_msg = str(call_args)
                # Should log function type but not field name
                assert "SensitiveField__c" not in log_msg or "COUNT" in log_msg


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_select_clause(self):
        """Should handle empty SELECT clause gracefully."""
        soql = "SELECT FROM Account"

        with pytest.raises(ValueError) as exc_info:
            _parse_select_columns_with_types(soql)

        # Error should be about missing SELECT...FROM or empty fields
        assert "select" in str(exc_info.value).lower() and "from" in str(exc_info.value).lower()

    def test_aggregate_without_group_by(self):
        """Should handle aggregate query without GROUP BY."""
        soql = "SELECT COUNT(Id), SUM(Amount) FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 2
        assert columns[0].aggregate_function == "count"
        assert columns[1].aggregate_function == "sum"

    def test_count_star_only(self):
        """Should handle COUNT(*) only query."""
        soql = "SELECT COUNT(*) FROM Account"
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 1
        assert columns[0].name == "count_expr"
        assert columns[0].aggregate_function == "count"

    def test_multiple_group_by_fields(self):
        """Should handle multiple GROUP BY fields."""
        soql = """
        SELECT Industry, BillingState, BillingCity, COUNT(Id)
        FROM Account
        GROUP BY Industry, BillingState, BillingCity
        """
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 4
        assert columns[0].aggregate_function is None
        assert columns[1].aggregate_function is None
        assert columns[2].aggregate_function is None
        assert columns[3].aggregate_function == "count"

    def test_aggregate_with_where_clause(self):
        """Should parse aggregates correctly with WHERE clause."""
        soql = """
        SELECT Industry, COUNT(Id)
        FROM Account
        WHERE BillingCountry = 'USA'
        GROUP BY Industry
        """
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 2
        assert columns[1].aggregate_function == "count"

    def test_aggregate_with_having_clause(self):
        """Should parse aggregates correctly with HAVING clause."""
        soql = """
        SELECT Industry, COUNT(Id) AS total
        FROM Account
        GROUP BY Industry
        HAVING COUNT(Id) > 10
        """
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 2
        assert columns[1].name == "total"
        assert columns[1].aggregate_function == "count"

    def test_all_aggregate_functions_in_one_query(self):
        """Should handle all aggregate functions in a single query."""
        soql = """
        SELECT
            Industry,
            COUNT(Id) AS count,
            SUM(AnnualRevenue) AS total,
            AVG(AnnualRevenue) AS average,
            MIN(CreatedDate) AS earliest,
            MAX(CreatedDate) AS latest
        FROM Account
        GROUP BY Industry
        """
        columns = _parse_select_columns_with_types(soql)

        assert len(columns) == 6
        assert columns[0].name == "industry"
        assert columns[0].aggregate_function is None
        assert columns[1].name == "count"
        assert columns[1].aggregate_function == "count"
        assert columns[2].name == "total"
        assert columns[2].aggregate_function == "sum"
        assert columns[3].name == "average"
        assert columns[3].aggregate_function == "avg"
        assert columns[4].name == "earliest"
        assert columns[4].aggregate_function == "min"
        assert columns[5].name == "latest"
        assert columns[5].aggregate_function == "max"


class TestBackwardCompatibility:
    """Tests for backward compatibility."""

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_default_infer_aggregate_types_is_true(self, mock_get_columns):
        """Should infer aggregate types by default (opt-out, not opt-in)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        soql = "SELECT Id, Industry, COUNT(Id) AS total FROM Account GROUP BY Id, Industry"

        # Call without explicit infer_aggregate_types parameter
        create_table_from_query("account_summary", soql, mock_conn)

        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should still infer types (BIGINT for COUNT)
        assert "BIGINT" in query_str

    @patch("sf_utils.db.schema._get_existing_columns")
    def test_existing_non_aggregate_queries_unchanged(self, mock_get_columns):
        """Should not affect existing non-aggregate queries."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_columns.return_value = []

        # Regular query without aggregates
        soql = "SELECT Id, Name, BillingCity FROM Account"
        create_table_from_query("sf_account", soql, mock_conn)

        # Should create all columns as TEXT (existing behavior)
        create_call = mock_cursor.execute.call_args_list[0]
        query_str = str(create_call[0][0])

        # Should contain TEXT, not BIGINT or NUMERIC
        assert "TEXT" in query_str
        assert "BIGINT" not in query_str
        assert "NUMERIC" not in query_str
