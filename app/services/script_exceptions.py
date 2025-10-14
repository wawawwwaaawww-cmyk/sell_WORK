"""Custom exceptions for the script service."""


class ScriptError(Exception):
    """Base exception for script service errors."""
    pass


class ExcelFormatError(ScriptError):
    """Exception raised for errors in the Excel file format."""
    pass


class IndexingError(ScriptError):
    """Exception raised for errors during the indexing process."""
    pass