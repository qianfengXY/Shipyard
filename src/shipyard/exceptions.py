"""Custom exceptions for Shipyard."""


class ShipyardError(Exception):
    """Base exception for Shipyard runtime failures."""


class ConfigError(ShipyardError):
    """Raised when runtime configuration is invalid."""


class StateStoreError(ShipyardError):
    """Raised when state persistence fails."""


class TaskParseError(ShipyardError):
    """Raised when TASKS.md cannot be parsed."""


class AdapterError(ShipyardError):
    """Raised when an adapter returns invalid data."""


class FinalReviewError(ShipyardError):
    """Raised when final review fails."""
