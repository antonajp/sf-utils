"""Tests for SOQL loader module."""

from datetime import date, datetime
from pathlib import Path

import pytest

from sf_utils.sync.soql_loader import load_soql, render_soql


class TestLoadSOQLHappyPath:
    """Tests for successful SOQL loading scenarios."""

    def test_load_soql_from_path_object(self, tmp_path):
        """Should load SOQL from pathlib.Path object."""
        soql_file = tmp_path / "test_query.soql"
        query = "SELECT Id, Name FROM Account WHERE IsActive = true"
        soql_file.write_text(query)

        result = load_soql(soql_file)

        assert result == query

    def test_load_soql_from_string_path(self, tmp_path):
        """Should load SOQL from string file path."""
        soql_file = tmp_path / "test_query.soql"
        query = "SELECT Id, Name FROM Contact"
        soql_file.write_text(query)

        result = load_soql(str(soql_file))

        assert result == query

    def test_load_soql_strips_whitespace(self, tmp_path):
        """Should strip leading and trailing whitespace."""
        soql_file = tmp_path / "whitespace.soql"
        query = "SELECT Id, Name FROM Account"
        soql_file.write_text(f"\n\n  {query}  \n\n")

        result = load_soql(soql_file)

        assert result == query
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_load_soql_multiline_query(self, tmp_path):
        """Should handle multi-line SOQL queries correctly."""
        soql_file = tmp_path / "multiline.soql"
        query = """SELECT Id, Name, Email
FROM Contact
WHERE Department = 'Sales'
ORDER BY Name"""
        soql_file.write_text(query)

        result = load_soql(soql_file)

        # Should preserve internal line structure but strip outer whitespace
        assert "FROM Contact" in result
        assert "WHERE Department" in result
        assert "ORDER BY Name" in result
        assert result == query.strip()


class TestLoadSOQLErrors:
    """Tests for error handling in SOQL loading."""

    def test_load_soql_file_not_found(self, tmp_path):
        """Should raise FileNotFoundError for missing files."""
        nonexistent = tmp_path / "does_not_exist.soql"

        with pytest.raises(FileNotFoundError) as exc_info:
            load_soql(nonexistent)

        assert "SOQL file not found" in str(exc_info.value)
        assert str(nonexistent) in str(exc_info.value)

    def test_load_soql_wrong_extension(self, tmp_path):
        """Should raise ValueError for non-.soql files."""
        wrong_extension = tmp_path / "query.txt"
        wrong_extension.write_text("SELECT Id FROM Account")

        with pytest.raises(ValueError) as exc_info:
            load_soql(wrong_extension)

        assert "must have .soql extension" in str(exc_info.value)
        assert ".txt" in str(exc_info.value)

    def test_load_soql_no_extension(self, tmp_path):
        """Should raise ValueError for files without extension."""
        no_extension = tmp_path / "query"
        no_extension.write_text("SELECT Id FROM Account")

        with pytest.raises(ValueError) as exc_info:
            load_soql(no_extension)

        assert "must have .soql extension" in str(exc_info.value)

    def test_load_soql_empty_file(self, tmp_path):
        """Should raise ValueError for empty files."""
        empty_file = tmp_path / "empty.soql"
        empty_file.write_text("")

        with pytest.raises(ValueError) as exc_info:
            load_soql(empty_file)

        assert "SOQL file is empty" in str(exc_info.value)

    def test_load_soql_whitespace_only_file(self, tmp_path):
        """Should raise ValueError for files with only whitespace."""
        whitespace_file = tmp_path / "whitespace_only.soql"
        whitespace_file.write_text("   \n\n\t  \n   ")

        with pytest.raises(ValueError) as exc_info:
            load_soql(whitespace_file)

        assert "SOQL file is empty" in str(exc_info.value)

    def test_load_soql_directory_instead_of_file(self, tmp_path):
        """Should raise ValueError when path is a directory."""
        dir_path = tmp_path / "query.soql"
        dir_path.mkdir()

        with pytest.raises(ValueError) as exc_info:
            load_soql(dir_path)

        assert "Path is a directory" in str(exc_info.value)


class TestLoadSOQLEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_load_soql_with_comments(self, tmp_path):
        """Should preserve SOQL with SQL-style comments (content preservation)."""
        # Note: SOQL doesn't officially support comments, but test content preservation
        soql_file = tmp_path / "with_comments.soql"
        query = """-- Query all active accounts
SELECT
    Id,
    Name,  -- Account name
    BillingCity
FROM Account
WHERE Active__c = true"""
        soql_file.write_text(query)

        result = load_soql(soql_file)

        # Should preserve comments as-is
        assert "-- Query all active accounts" in result
        assert "-- Account name" in result
        assert result == query.strip()

    def test_load_soql_unicode_content(self, tmp_path):
        """Should handle Unicode characters in query."""
        soql_file = tmp_path / "unicode.soql"
        query = "SELECT Id, Name FROM Account WHERE Name = 'Café Résumé'"
        soql_file.write_text(query, encoding="utf-8")

        result = load_soql(soql_file)

        assert result == query
        assert "Café Résumé" in result

    def test_load_soql_windows_line_endings(self, tmp_path):
        """Should handle Windows CRLF line endings."""
        soql_file = tmp_path / "windows.soql"
        # Write CRLF line endings explicitly
        query_crlf = "SELECT\r\n    Id,\r\n    Name\r\nFROM Account"
        soql_file.write_bytes(query_crlf.encode("utf-8"))

        result = load_soql(soql_file)

        # Should load successfully with line structure preserved
        assert "SELECT" in result
        assert "Id" in result
        assert "Name" in result
        assert "FROM Account" in result

    def test_load_soql_complex_query_with_subqueries(self, tmp_path):
        """Should handle complex SOQL with subqueries."""
        soql_file = tmp_path / "complex.soql"
        query = """SELECT
    Id,
    Name,
    (SELECT Id, Name FROM Contacts),
    (SELECT Amount FROM Opportunities WHERE StageName = 'Closed Won')
FROM Account
WHERE Industry = 'Technology'
    AND AnnualRevenue > 1000000
ORDER BY Name
LIMIT 100"""
        soql_file.write_text(query)

        result = load_soql(soql_file)

        assert "SELECT Id, Name FROM Contacts" in result
        assert "WHERE Industry = 'Technology'" in result
        assert "LIMIT 100" in result

    def test_load_soql_case_insensitive_extension(self, tmp_path):
        """Should accept .SOQL extension (case insensitive)."""
        soql_file = tmp_path / "query.SOQL"
        query = "SELECT Id FROM Account"
        soql_file.write_text(query)

        result = load_soql(soql_file)

        assert result == query

    def test_load_soql_with_special_characters(self, tmp_path):
        """Should handle special characters in SOQL."""
        soql_file = tmp_path / "special_chars.soql"
        query = "SELECT Id, Custom__c FROM Object__c WHERE Name LIKE '%Test & Co.%'"
        soql_file.write_text(query)

        result = load_soql(soql_file)

        assert result == query
        assert "Custom__c" in result
        assert "Object__c" in result
        assert "&" in result

    def test_load_soql_deep_nested_path(self, tmp_path):
        """Should handle deeply nested directory paths."""
        nested_dir = tmp_path / "soql" / "queries" / "accounts" / "active"
        nested_dir.mkdir(parents=True)
        soql_file = nested_dir / "query.soql"
        query = "SELECT Id, Name FROM Account WHERE Active__c = true"
        soql_file.write_text(query)

        result = load_soql(soql_file)

        assert result == query


class TestRenderSOQLHappyPath:
    """Tests for successful SOQL template rendering scenarios."""

    def test_render_single_variable(self):
        """Should substitute a single variable in template."""
        template = "SELECT Id FROM Account WHERE Name = '${account_name}'"
        params = {"account_name": "Acme Corp"}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE Name = 'Acme Corp'"

    def test_render_multiple_variables(self):
        """Should substitute multiple variables in template."""
        template = "SELECT Id FROM ${object_name} WHERE Name = '${name}' AND Type = '${type}'"
        params = {
            "object_name": "Account",
            "name": "Acme Corp",
            "type": "Customer"
        }

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE Name = 'Acme Corp' AND Type = 'Customer'"

    def test_render_datetime_iso8601(self):
        """Should convert datetime to ISO8601 format (YYYY-MM-DDTHH:MM:SSZ)."""
        template = "SELECT Id FROM Account WHERE CreatedDate >= ${start_date}"
        params = {"start_date": datetime(2024, 1, 15, 14, 30, 45)}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE CreatedDate >= 2024-01-15T14:30:45Z"

    def test_render_date_format(self):
        """Should convert date to YYYY-MM-DD format."""
        template = "SELECT Id FROM Account WHERE CreatedDate >= ${start_date}"
        params = {"start_date": date(2024, 3, 15)}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE CreatedDate >= 2024-03-15"

    def test_render_bool_lowercase(self):
        """Should render boolean as lowercase true/false for SOQL compatibility."""
        template = "SELECT Id FROM Account WHERE IsActive = ${active} AND IsDeleted = ${deleted}"
        params = {"active": True, "deleted": False}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE IsActive = true AND IsDeleted = false"

    def test_render_none_to_null(self):
        """Should convert None to 'null' string for SOQL."""
        template = "SELECT Id FROM Account WHERE ParentId = ${parent_id}"
        params = {"parent_id": None}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE ParentId = null"

    def test_render_int_float(self):
        """Should handle integer and float numeric types."""
        template = "SELECT Id FROM Opportunity WHERE Amount >= ${min_amount} AND Probability = ${probability}"
        params = {"min_amount": 100000, "probability": 0.75}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Opportunity WHERE Amount >= 100000 AND Probability = 0.75"


class TestRenderSOQLErrors:
    """Tests for error handling in SOQL template rendering."""

    def test_render_missing_variable_strict(self):
        """Should raise KeyError when required variable is missing and strict=True."""
        template = "SELECT Id FROM Account WHERE Name = '${account_name}' AND Type = '${account_type}'"
        params = {"account_name": "Acme Corp"}  # Missing account_type

        with pytest.raises(KeyError) as exc_info:
            render_soql(template, params, strict=True)

        error_msg = str(exc_info.value)
        assert "account_type" in error_msg

    def test_render_missing_variable_non_strict(self):
        """Should preserve placeholder when variable is missing and strict=False."""
        template = "SELECT Id FROM Account WHERE Name = '${account_name}' AND Type = '${account_type}'"
        params = {"account_name": "Acme Corp"}  # Missing account_type

        result = render_soql(template, params, strict=False)

        assert "Acme Corp" in result
        assert "${account_type}" in result  # Placeholder preserved

    def test_render_unsupported_type_list(self):
        """Should raise TypeError for unsupported list type."""
        template = "SELECT Id FROM Account WHERE Id IN ${account_ids}"
        params = {"account_ids": ["001xxx", "002yyy"]}

        with pytest.raises(TypeError) as exc_info:
            render_soql(template, params)

        error_msg = str(exc_info.value)
        assert "unsupported type" in error_msg.lower() or "list" in error_msg.lower()

    def test_render_unsupported_type_dict(self):
        """Should raise TypeError for unsupported dict type."""
        template = "SELECT Id FROM Account WHERE Name = '${config}'"
        params = {"config": {"key": "value"}}

        with pytest.raises(TypeError) as exc_info:
            render_soql(template, params)

        error_msg = str(exc_info.value)
        assert "unsupported type" in error_msg.lower() or "dict" in error_msg.lower()


class TestRenderSOQLEdgeCases:
    """Tests for edge cases and special scenarios in SOQL rendering."""

    def test_render_empty_template(self):
        """Should handle empty template string."""
        template = ""
        params = {"unused": "value"}

        result = render_soql(template, params)

        assert result == ""

    def test_render_no_variables(self):
        """Should return template unchanged when no variables are present."""
        template = "SELECT Id, Name FROM Account WHERE IsActive = true"
        params = {"unused": "value"}

        result = render_soql(template, params)

        assert result == template

    def test_render_duplicate_variables(self):
        """Should substitute the same variable multiple times correctly."""
        template = "SELECT Id FROM Account WHERE Name = '${name}' OR BillingCity = '${name}'"
        params = {"name": "Acme"}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE Name = 'Acme' OR BillingCity = 'Acme'"
        assert result.count("Acme") == 2

    def test_render_unicode_values(self):
        """Should handle Unicode characters in parameter values."""
        template = "SELECT Id FROM Account WHERE Name = '${company_name}'"
        params = {"company_name": "Café Résumé & Co. 日本"}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE Name = 'Café Résumé & Co. 日本'"
        assert "Café" in result
        assert "日本" in result

    def test_render_datetime_with_microseconds(self):
        """Should format datetime with microseconds correctly (truncated to seconds)."""
        template = "SELECT Id FROM Account WHERE CreatedDate >= ${timestamp}"
        params = {"timestamp": datetime(2024, 1, 1, 12, 0, 0, 123456)}

        result = render_soql(template, params)

        # ISO8601 format should only include seconds, not microseconds
        assert result == "SELECT Id FROM Account WHERE CreatedDate >= 2024-01-01T12:00:00Z"

    def test_render_mixed_types(self):
        """Should handle mixed parameter types in single template."""
        template = """SELECT Id FROM Opportunity
WHERE CreatedDate >= ${start_date}
  AND Amount >= ${min_amount}
  AND StageName = '${stage}'
  AND IsClosed = ${is_closed}
  AND Type = ${type_filter}"""
        params = {
            "start_date": datetime(2024, 1, 1),
            "min_amount": 50000,
            "stage": "Prospecting",
            "is_closed": False,
            "type_filter": None
        }

        result = render_soql(template, params)

        assert "2024-01-01T00:00:00Z" in result
        assert "50000" in result
        assert "Prospecting" in result
        assert "false" in result
        assert "null" in result

    def test_render_empty_params_with_variables(self):
        """Should raise KeyError when params is empty but template has variables."""
        template = "SELECT Id FROM Account WHERE Name = '${name}'"
        params = {}

        with pytest.raises(KeyError):
            render_soql(template, params, strict=True)

    def test_render_empty_params_no_variables(self):
        """Should succeed when params is empty and template has no variables."""
        template = "SELECT Id FROM Account WHERE IsActive = true"
        params = {}

        result = render_soql(template, params)

        assert result == template

    def test_render_string_with_special_characters(self):
        """Should preserve special characters in string values."""
        template = "SELECT Id FROM Account WHERE Name LIKE '${pattern}'"
        params = {"pattern": "%Test & Co.%"}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Account WHERE Name LIKE '%Test & Co.%'"
        assert "&" in result
        assert "%" in result

    def test_render_numeric_zero_values(self):
        """Should handle zero values correctly (not confused with False/None)."""
        template = "SELECT Id FROM Opportunity WHERE Amount = ${amount} AND Probability = ${prob}"
        params = {"amount": 0, "prob": 0.0}

        result = render_soql(template, params)

        assert "Amount = 0" in result
        assert "Probability = 0.0" in result

    def test_render_negative_numbers(self):
        """Should handle negative numeric values."""
        template = "SELECT Id FROM Transaction WHERE Amount = ${amount}"
        params = {"amount": -500.50}

        result = render_soql(template, params)

        assert result == "SELECT Id FROM Transaction WHERE Amount = -500.5"

    def test_render_date_range_realistic_example(self):
        """Should handle realistic date range query scenario."""
        template = """SELECT Id, Name, CreatedDate
FROM Account
WHERE CreatedDate >= ${start_date}
  AND CreatedDate < ${end_date}
  AND IsActive = ${active}"""
        params = {
            "start_date": datetime(2024, 1, 1, 0, 0, 0),
            "end_date": datetime(2024, 2, 1, 0, 0, 0),
            "active": True
        }

        result = render_soql(template, params)

        assert "2024-01-01T00:00:00Z" in result
        assert "2024-02-01T00:00:00Z" in result
        assert "IsActive = true" in result
