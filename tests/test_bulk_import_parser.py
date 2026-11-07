"""CSV parser and validator tests for bulk lead imports."""

import io

import pytest

from app.services.bulk_import_parser import (
    parse_csv_file,
    validate_row,
    validate_all_rows,
    ParseError,
)


def test_parse_csv_file():
    """Verify CSV parsing extracts headers and rows."""
    csv_data = """client_name,email,company,title,phone,source,monthly_retainer,payment_frequency
John Doe,john@example.com,Acme Inc,CEO,555-1234,linkedin,1000,monthly
Jane Smith,jane@example.com,TechCorp,CTO,555-5678,apollo,2000,quarterly"""

    f = io.StringIO(csv_data)
    result = parse_csv_file(f)

    assert 'email' in result.headers
    assert 'client_name' in result.headers
    assert len(result.rows) == 2
    assert result.rows[0]['email'] == 'john@example.com'
    assert result.rows[1]['title'] == 'cto'  # normalized lowercase


def test_parse_csv_empty_file():
    """Verify empty CSV raises error."""
    csv_data = ""

    f = io.StringIO(csv_data)
    with pytest.raises(ParseError):
        parse_csv_file(f)


def test_parse_csv_missing_email_column():
    """Verify CSV missing required email column raises error."""
    csv_data = """client_name,company,title
John Doe,Acme Inc,CEO"""

    f = io.StringIO(csv_data)
    with pytest.raises(ParseError, match="email"):
        parse_csv_file(f)


def test_validate_row_success():
    """Verify valid row passes validation."""
    row = {
        'client_name': 'John Doe',
        'email': 'john@example.com',
        'company': 'Acme Inc',
        'title': 'CEO',
        'phone': '555-1234',
        'source': 'manual_import',
        'monthly_retainer': '1000',
        'payment_frequency': 'monthly',
    }

    errors = validate_row(row, row_number=1)
    assert errors == []


def test_validate_row_missing_email():
    """Verify row without email fails."""
    row = {
        'client_name': 'John Doe',
        'email': '',
        'company': 'Acme Inc',
        'title': 'CEO',
    }

    errors = validate_row(row, row_number=1)
    assert any('email' in e.lower() for e in errors)


def test_validate_row_invalid_email():
    """Verify invalid email format fails."""
    row = {
        'client_name': 'John Doe',
        'email': 'not-an-email',
        'company': 'Acme Inc',
    }

    errors = validate_row(row, row_number=1)
    assert any('email' in e.lower() and 'invalid' in e.lower() for e in errors)


def test_validate_row_invalid_retainer():
    """Verify non-numeric retainer fails."""
    row = {
        'client_name': 'John Doe',
        'email': 'john@example.com',
        'company': 'Acme Inc',
        'monthly_retainer': 'not-a-number',
    }

    errors = validate_row(row, row_number=1)
    assert any('retainer' in e.lower() and 'number' in e.lower() for e in errors)


def test_validate_row_invalid_frequency():
    """Verify invalid payment frequency fails."""
    row = {
        'client_name': 'John Doe',
        'email': 'john@example.com',
        'payment_frequency': 'weekly',
    }

    errors = validate_row(row, row_number=1)
    assert any('frequency' in e.lower() for e in errors)


def test_validate_all_rows():
    """Verify validate_all_rows separates valid from invalid."""
    rows = [
        {'client_name': 'John', 'email': 'john@example.com'},  # valid
        {'client_name': 'Jane', 'email': 'invalid-email'},     # invalid
        {'client_name': 'Bob', 'email': 'bob@example.com'},    # valid
    ]

    valid, errors = validate_all_rows(rows)

    assert len(valid) == 2
    assert len(errors) == 1
    assert errors[0][0] == 3  # row number (1-indexed header, so row 2 is index 3)
