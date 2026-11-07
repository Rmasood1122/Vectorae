"""CSV parser and validator for bulk lead imports."""

import csv
import re
from dataclasses import dataclass
from typing import List, Dict, Tuple


class ParseError(Exception):
    """Raised when CSV parsing fails."""
    pass


@dataclass
class ParseResult:
    """Result of parsing a CSV file."""
    headers: List[str]
    rows: List[Dict[str, str]]


# Valid payment frequencies
VALID_FREQUENCIES = {'monthly', 'quarterly', 'annual', 'one-time'}

# Email regex pattern
EMAIL_PATTERN = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'


def parse_csv_file(file_obj) -> ParseResult:
    """Parse CSV file and return headers and rows.

    Args:
        file_obj: File-like object containing CSV data

    Returns:
        ParseResult with headers list and normalized rows

    Raises:
        ParseError: If CSV is empty or missing required fields
    """
    reader = csv.DictReader(file_obj)

    if reader.fieldnames is None or len(reader.fieldnames) == 0:
        raise ParseError("CSV file is empty")

    # Check for required email column
    fieldnames_lower = [f.lower() for f in reader.fieldnames]
    if 'email' not in fieldnames_lower:
        raise ParseError("CSV must contain 'email' column")

    headers = fieldnames_lower
    rows = []

    for row in reader:
        # Normalize: lowercase keys and values
        normalized_row = {}
        for key, value in row.items():
            normalized_key = key.lower()
            normalized_value = value.lower() if value else value
            normalized_row[normalized_key] = normalized_value
        rows.append(normalized_row)

    if len(rows) == 0:
        raise ParseError("CSV file has no data rows")

    return ParseResult(headers=headers, rows=rows)


def validate_row(row: Dict[str, str], row_number: int) -> List[str]:
    """Validate a single CSV row.

    Args:
        row: Dictionary representing one CSV row
        row_number: 1-indexed row number (for error messages)

    Returns:
        List of error strings. Empty list if valid.
    """
    errors = []

    # Check required email
    email = row.get('email', '').strip()
    if not email:
        errors.append(f"Row {row_number}: email is required")
    else:
        # Validate email format
        if not re.match(EMAIL_PATTERN, email):
            errors.append(f"Row {row_number}: email is invalid format")

    # Validate monthly_retainer if present
    retainer = row.get('monthly_retainer', '').strip()
    if retainer:
        try:
            float(retainer)
        except ValueError:
            errors.append(f"Row {row_number}: monthly_retainer must be a number")

    # Validate payment_frequency if present
    frequency = row.get('payment_frequency', '').strip()
    if frequency and frequency not in VALID_FREQUENCIES:
        errors.append(f"Row {row_number}: payment_frequency must be one of {sorted(VALID_FREQUENCIES)}")

    return errors


def validate_all_rows(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Tuple[int, List[str]]]]:
    """Validate all rows in a list.

    Args:
        rows: List of row dictionaries

    Returns:
        Tuple of (valid_rows, errors_list) where errors_list contains
        (1-indexed_row_number, error_strings) for invalid rows
    """
    valid_rows = []
    errors_list = []

    for idx, row in enumerate(rows):
        # Row numbers are 1-indexed, with header as row 1, first data row as 2
        row_number = idx + 2

        row_errors = validate_row(row, row_number)
        if row_errors:
            errors_list.append((row_number, row_errors))
        else:
            valid_rows.append(row)

    return valid_rows, errors_list
