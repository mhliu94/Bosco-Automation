class BoscoError(RuntimeError):
    """Base error for expected automation failures."""


class ConfigurationError(BoscoError):
    """Configuration is missing or invalid."""


class SafetyError(BoscoError):
    """A fail-closed safety check prevented an action."""
