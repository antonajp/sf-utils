"""Tests for SOQL loader module."""

from pathlib import Path

import pytest

from sf_utils.sync.soql_loader import load_soql


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
