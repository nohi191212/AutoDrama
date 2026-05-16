class AutoDramaError(Exception):
    """Base application error."""


class ProviderError(AutoDramaError):
    """Base provider error."""


class ProviderAuthError(ProviderError):
    """Raised when a provider credential is missing or rejected."""


class ProviderBadResponseError(ProviderError):
    """Raised when a provider response cannot be parsed or validated."""


class WorkflowError(AutoDramaError):
    """Raised when a workflow cannot proceed."""
